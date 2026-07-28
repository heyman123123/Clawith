"""AO workflow REST surface for HR / project views.

P1.4 keeps the REST layer deliberately small — the scheduler tool registry
already exposes every meaningful action. The HTTP endpoints only exist so
the frontend (or external integrations) can:

* re-parse a workflow YAML (cheap validation before promotion);
* kick off (or resume) an AO run while the chat runtime is offline;
* poll the combined AO + DB status of a run;
* resolve a pending approval decision (P2.4) and list awaiting steps
  so the front-end approval card can show the open queue per workflow.

All endpoints share the same tenant isolation: callers must be a member
of the workflow's tenant. AO is deliberately not invoked in tests — the
``AOClient`` is replaced via ``app.dependency_overrides``-style
monkeypatching from the test harness.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import get_current_user
from app.database import get_db
from app.models.project import ProjectDecision, ProjectWorkflow
from app.models.user import User
from app.services.ao import run_repository
from app.services.ao.approval_node import (
    resolve_approval as resolve_approval_node,
)
from app.services.ao.approval_node import (
    trigger_approval_node as trigger_approval_node_service,
)
from app.services.ao.client import AOClient
from app.services.ao.scheduler_tools import AOIntegrationError

if TYPE_CHECKING:
    from app.models.workflow_run import WorkflowRunStep


router = APIRouter(prefix="/api/ao", tags=["ao"])


class ParseWorkflowIn(BaseModel):
    yaml_content: str = Field(min_length=1, max_length=120_000)


class ParseWorkflowOut(BaseModel):
    name: str
    agents_dir: str
    llm: dict
    steps: list[dict]


class RunWorkflowIn(BaseModel):
    workflow_id: uuid.UUID
    inputs: dict[str, str] | None = None
    resume: str | None = None
    from_step: str | None = None


class RunWorkflowOut(BaseModel):
    workflow_id: str
    run_id: str
    returncode: int
    stdout: str
    stderr: str
    metadata_path: str | None
    run_dir: str | None


class RunStatusOut(BaseModel):
    workflow_id: str
    run_id: str
    run_status: str
    started_at: str | None
    completed_at: str | None
    ao_status: dict
    steps: list[dict]


def _ao_workflows_dir() -> Path:
    """Resolve the workflows directory from the live settings."""
    cfg = get_settings()
    base = Path(cfg.AO_WORKFLOWS_DIR or "")
    if not base:
        base = Path(cfg.AO_HOME_DIR or ".") / "workflows"
    return base


def _ao_output_dir() -> Path:
    """Resolve the per-workflow output root from the live settings."""
    cfg = get_settings()
    base = Path(cfg.AO_OUTPUT_DIR or "")
    if not base:
        base = Path(cfg.AO_HOME_DIR or ".") / "output"
    return base


def _workflow_yaml_path(workflow_id: uuid.UUID) -> Path:
    """Return ``<AO_WORKFLOWS_DIR>/<workflow_id>.yaml`` and create the parent."""
    base = _ao_workflows_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{workflow_id}.yaml"


async def _load_authorized_workflow(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    current_user: User,
) -> ProjectWorkflow:
    """Return the workflow only if it belongs to the caller's tenant.

    AO endpoints do not require admin role — the project lead or scheduler
    are the operators. ``creator_id`` is intentionally not part of the
    predicate so HR-lead delegated users can resume a workflow started by
    someone else in the same tenant.
    """
    if current_user.tenant_id is None:
        raise HTTPException(status_code=403, detail="A tenant is required for AO workflows")
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == current_user.tenant_id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


async def _load_run_row(db: AsyncSession, *, workflow_id: uuid.UUID):
    """Return the canonical run row for a workflow.

    P1.3 uses the ``ProjectWorkflow.id`` itself as the run id, but the
    caller may still want a separate ``WorkflowRun`` row in the future.
    The helper isolates that lookup so tests can override it without
    monkeypatching the AO client.
    """
    # P1.3 ships a placeholder; tests replace this helper.
    return await db.scalar(select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id))


async def _mark_run_started(db: AsyncSession, *, workflow_id: uuid.UUID) -> None:
    """Delegate to the P1.3 repository so dashboards show the workflow as active."""
    await run_repository.mark_run_started(db, workflow_id=workflow_id)


async def _get_run_steps(db: AsyncSession, *, run_id: uuid.UUID) -> list[WorkflowRunStep]:
    """Return the canonical run steps for a workflow.

    In production ``run_id`` is the same UUID as ``workflow_id``; the
    parameter name matches the API response field so consumers can read
    ``run_id`` without an extra mapping.  P1.3 keys steps on
    ``workflow_id`` so the underlying repository uses that key.
    """
    return await run_repository.get_run_steps(db, workflow_id=run_id)


def _step_to_response(step) -> dict:
    """Render a ``WorkflowRunStep`` row as the JSON shape the API returns."""
    return {
        "id": str(step.id),
        "step_id": step.step_key,
        "order": step.step_order,
        "role": step.role_path,
        "status": step.status,
        "depends_on": list(step.depends_on or []),
        "output": step.output_var,
        "quality_score": step.quality_score,
        "retry_count": step.retry_count,
    }


@router.post("/parse", response_model=ParseWorkflowOut)
async def parse_workflow(
    body: ParseWorkflowIn,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ParseWorkflowOut:
    """Parse + validate an AO YAML body and return the typed projection.

    The endpoint does not write the YAML to disk — that responsibility
    stays in P1.3 ``workflow_composer``. P1.4 uses this for editor
    previews and ad-hoc validation.
    """
    del current_user  # auth gate only
    client = AOClient()
    try:
        parsed = client.parse_workflow(body.yaml_content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dumped = parsed.model_dump() if hasattr(parsed, "model_dump") else {}
    return ParseWorkflowOut(
        name=dumped.get("name", getattr(parsed, "name", "")),
        agents_dir=dumped.get("agents_dir", getattr(parsed, "agents_dir", "")),
        llm=dumped.get("llm", {}),
        steps=[
            {"id": step.get("id"), "role": step.get("role"), "task": step.get("task")}
            for step in dumped.get("steps", [])
        ],
    )


@router.post("/runs", response_model=RunWorkflowOut)
async def run_workflow(
    body: RunWorkflowIn,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RunWorkflowOut:
    """Kick off an AO run for a workflow previously composed by P1.3.

    The endpoint persists the AO YAML to ``<AO_WORKFLOWS_DIR>/<workflow_id>.yaml``
    if it does not already exist so the CLI has a stable path.  The
    resulting ``run_id`` is the ``ProjectWorkflow.id``; P1.3 will introduce
    a dedicated ``WorkflowRun`` row once multi-run workflows are needed.
    """
    workflow = await _load_authorized_workflow(db, workflow_id=body.workflow_id, current_user=current_user)
    run = await _load_run_row(db, workflow_id=workflow.id)
    yaml_path = _workflow_yaml_path(workflow.id)
    yaml_content = getattr(workflow, "yaml_content", None) or getattr(run, "yaml_content", None)
    if yaml_content and not yaml_path.is_file():
        yaml_path.write_text(yaml_content, encoding="utf-8")
    if not yaml_path.is_file():
        raise HTTPException(
            status_code=409,
            detail="Workflow YAML is not available; wait for P1.3 to finish composing.",
        )

    asset_dir = getattr(run, "asset_dir_path", None) or getattr(workflow, "asset_dir_path", None)
    output_dir = Path(asset_dir) if asset_dir else _ao_output_dir() / str(workflow.id)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = AOClient()
    result = client.run(
        yaml_path,
        inputs=body.inputs,
        output_dir=output_dir,
        resume=body.resume,
        from_step=body.from_step,
    )
    if hasattr(workflow, "ao_run_dir"):
        workflow.ao_run_dir = str(output_dir)
    await _mark_run_started(db, workflow_id=workflow.id)
    await db.flush()
    run_id = getattr(run, "id", workflow.id)
    return RunWorkflowOut(
        workflow_id=str(workflow.id),
        run_id=str(run_id),
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        metadata_path=str(result.metadata_path) if getattr(result, "metadata_path", None) is not None else None,
        run_dir=str(result.output_dir) if getattr(result, "output_dir", None) is not None else None,
    )


@router.get("/runs/{workflow_id}/status", response_model=RunStatusOut)
async def get_run_status(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RunStatusOut:
    """Combine AO's local metadata with the database step ledger."""
    workflow = await _load_authorized_workflow(db, workflow_id=workflow_id, current_user=current_user)
    run = await _load_run_row(db, workflow_id=workflow.id)
    run_dir_path = (
        getattr(workflow, "ao_run_dir", None) or getattr(run, "asset_dir_path", None) or getattr(run, "run_dir", None)
    )
    run_dir = Path(run_dir_path) if run_dir_path else None
    client = AOClient()
    if run_dir is not None:
        try:
            ao_status = client.get_status(run_dir)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"AO status read failed: {exc}") from exc
        ao_payload = ao_status.model_dump() if hasattr(ao_status, "model_dump") else {}
    else:
        ao_payload = {"state": "unknown"}
    run_id = getattr(run, "id", workflow.id)
    steps = await _get_run_steps(db, run_id=run_id)
    return RunStatusOut(
        workflow_id=str(workflow.id),
        run_id=str(run_id),
        run_status=getattr(run, "status", None) or getattr(workflow, "status", "unknown"),
        started_at=getattr(run, "started_at", None).isoformat() if getattr(run, "started_at", None) else None,
        completed_at=getattr(run, "completed_at", None).isoformat() if getattr(run, "completed_at", None) else None,
        ao_status=ao_payload,
        steps=[_step_to_response(step) for step in steps],
    )


# ---------------------------------------------------------------------------
# P2.4 — human approval card surface
# ---------------------------------------------------------------------------


class TriggerApprovalIn(BaseModel):
    """Body for ``POST /api/ao/workflows/{workflow_id}/approvals``."""

    step_id: uuid.UUID = Field(..., description="WorkflowRunStep.id awaiting approval")
    prompt: str = Field(..., min_length=1, max_length=2000)
    approver_user_ids: list[uuid.UUID] = Field(..., min_length=1)


class TriggerApprovalOut(BaseModel):
    decision_id: str
    step_id: str
    group_id: str
    group_message_id: str
    approver_user_ids: list[str]


class ResolveApprovalIn(BaseModel):
    """Body for ``POST /api/ao/decisions/{decision_id}/resolve``."""

    response_text: str = Field(default="", max_length=4000)
    approved: bool


class ResolveApprovalOut(BaseModel):
    decision_id: str
    step_id: str
    approved: bool
    step_status: str


class PendingApprovalOut(BaseModel):
    workflow_id: str
    pending_steps: list[dict]
    decisions: list[dict]


def _decision_to_dict(decision: ProjectDecision) -> dict:
    """Render a ``ProjectDecision`` row as JSON for the API response."""
    return {
        "id": str(decision.id),
        "workflow_id": str(decision.workflow_id),
        "group_id": str(decision.group_id),
        "title": decision.title,
        "status": decision.status,
        "response": decision.response,
        "responded_at": decision.responded_at.isoformat() if decision.responded_at else None,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
    }


@router.post(
    "/workflows/{workflow_id}/approvals",
    response_model=TriggerApprovalOut,
)
async def trigger_approval(
    workflow_id: uuid.UUID,
    body: TriggerApprovalIn,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> TriggerApprovalOut:
    """POST a new approval card into the workflow's execution group.

    The endpoint is the HTTP twin of
    :func:`scheduler_tools.trigger_approval_node` for the front-end
    "manual approval trigger" button. ``approver_user_ids`` must be
    valid tenant users; the service resolves them to ``Participant``
    rows before composing the @-mentions.
    """
    await _load_authorized_workflow(db, workflow_id=workflow_id, current_user=current_user)
    try:
        result = await trigger_approval_node_service(
            db,
            workflow_id=workflow_id,
            step_id=body.step_id,
            prompt=body.prompt,
            approver_user_ids=list(body.approver_user_ids),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AOIntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TriggerApprovalOut(
        decision_id=result["decision_id"],
        step_id=result["step_id"],
        group_id=result["group_id"],
        group_message_id=result["group_message_id"],
        approver_user_ids=result["approver_user_ids"],
    )


@router.post(
    "/decisions/{decision_id}/resolve",
    response_model=ResolveApprovalOut,
)
async def resolve_approval(
    decision_id: uuid.UUID,
    body: ResolveApprovalIn,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> ResolveApprovalOut:
    """Record a user response and either resume the AO step or mark it failed.

    Tenant isolation is enforced by looking up the decision's workflow
    and confirming the caller belongs to the same tenant. AO is never
    invoked in tests — ``scheduler_tools.ao_resume_from_step`` is
    monkeypatched from the test harness.
    """
    decision = await db.get(ProjectDecision, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    await _load_authorized_workflow(db, workflow_id=decision.workflow_id, current_user=current_user)
    try:
        result = await resolve_approval_node(
            db,
            decision_id=decision_id,
            response_text=body.response_text,
            approved=body.approved,
        )
    except AOIntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResolveApprovalOut(
        decision_id=result["decision_id"],
        step_id=result["step_id"],
        approved=result["approved"],
        step_status=result["step_status"],
    )


@router.get(
    "/workflows/{workflow_id}/pending-approvals",
    response_model=PendingApprovalOut,
)
async def list_pending_approvals(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PendingApprovalOut:
    """List steps still awaiting human approval + the matching decisions.

    The response pairs every ``WorkflowRunStep`` with ``status='awaiting_approval'``
    to the ``ProjectDecision`` rows that reference the same step, so the
    front-end can render a single timeline per workflow.
    """
    await _load_authorized_workflow(db, workflow_id=workflow_id, current_user=current_user)

    pending_steps = await run_repository.get_run_steps(db, workflow_id=workflow_id)
    pending_steps = [step for step in pending_steps if getattr(step, "status", None) == "awaiting_approval"]

    decisions = list(
        (
            await db.execute(
                select(ProjectDecision)
                .where(
                    ProjectDecision.workflow_id == workflow_id,
                    ProjectDecision.status == "pending",
                )
                .order_by(ProjectDecision.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return PendingApprovalOut(
        workflow_id=str(workflow_id),
        pending_steps=[_step_to_response(step) for step in pending_steps],
        decisions=[_decision_to_dict(decision) for decision in decisions],
    )
