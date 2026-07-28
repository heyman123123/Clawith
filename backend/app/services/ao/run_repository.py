"""Persistence layer for AO workflow runs and their DAG steps.

This is the P1.3 counterpart to ``workflow_composer``: while the composer
writes the YAML file, ``run_repository`` mirrors the DAG into
``workflow_run_steps`` (created by migration
``202607271300_add_ao_workflow_runs``) so P1.4 can:

* render the run timeline from a DB query instead of re-parsing YAML,
* resume from any step by looking up its ``agent_id`` / ``role_path`` /
  ``depends_on``,
* record quality scores and per-step tokens without scanning
  ``ao-output``.

The repository deliberately accepts an existing ``ProjectWorkflow`` row — the
provisioning flow already owns the transaction; this layer only stages the
``add()`` calls and ``flush()``s once at the end. P1.4 may extend this file
with retry / resume helpers; P1.3 only ships the minimal create + list +
mark-started helpers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from app.models.project import ProjectWorkflow
from app.models.workflow_run import WorkflowRunStep

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_DEFAULT_DAG_STEPS: tuple[dict, ...] = (
    {
        "step_key": "clarify",
        "step_order": 0,
        "role_path": "product/project-scheduler",
        "task_summary": "把需求拆成 3~5 个执行步骤",
        "output_var": "plan",
        "depends_on": [],
        "acceptance_text": "输出包含可执行的下游任务清单",
    },
    {
        "step_key": "execute",
        "step_order": 1,
        "role_path": "product/executor-0",
        "task_summary": "执行计划 {{plan}}",
        "output_var": "artifact",
        "depends_on": ["clarify"],
        "acceptance_text": "产出物落盘到工作流实例目录",
    },
    {
        "step_key": "review",
        "step_order": 2,
        "role_path": "quality/quality-reviewer",
        "task_summary": "对 {{artifact}} 做质检，输出 score 0~100",
        "output_var": "review",
        "depends_on": ["execute"],
        "acceptance_text": "输出包含 score 与 feedback",
    },
)


async def create_run_row(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    yaml_text: str,
    run_dir: Path,
    agent_ids: dict[str, uuid.UUID] | None = None,
) -> list[WorkflowRunStep]:
    """Insert one ``WorkflowRunStep`` row per default DAG node.

    The function takes the already-flushed ``ProjectWorkflow`` from the
    provisioning transaction, then stages three ``WorkflowRunStep`` rows
    (clarify → execute → review) that mirror the YAML. ``agent_ids`` is the
    optional four-power slot mapping; missing keys leave ``agent_id`` null so
    P1.4 can rebind once it loads the run.

    Returns the staged rows in deterministic ``step_order`` ascending order so
    callers can render the timeline without re-querying.
    """
    agent_ids = agent_ids or {}
    rows: list[WorkflowRunStep] = []
    for template in _DEFAULT_DAG_STEPS:
        step_key = str(template["step_key"])
        agent_id = _agent_id_for_step(step_key, agent_ids)
        row = WorkflowRunStep(
            id=uuid.uuid4(),
            tenant_id=workflow.tenant_id,
            workflow_id=workflow.id,
            step_key=step_key,
            step_order=int(template["step_order"]),
            role_path=str(template["role_path"]),
            agent_id=agent_id,
            task_summary=str(template["task_summary"]),
            output_var=str(template["output_var"]) if template.get("output_var") else None,
            depends_on=list(template["depends_on"]),
            acceptance_text=str(template["acceptance_text"]) if template.get("acceptance_text") else None,
            status="pending",
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    logger.info(
        "[AORunRepo] Seeded {} run steps for workflow {} (run_dir={})",
        len(rows),
        workflow.id,
        run_dir,
    )
    return rows


def _agent_id_for_step(
    step_key: str,
    agent_ids: dict[str, uuid.UUID],
) -> uuid.UUID | None:
    """Map a DAG step to the corresponding power-slot Agent id, if any."""
    mapping = {
        "clarify": "scheduler",
        "execute": "executor_0",
        "review": "quality",
    }
    role_key = mapping.get(step_key)
    if not role_key:
        return None
    value = agent_ids.get(role_key)
    return value if isinstance(value, uuid.UUID) else None


async def get_run_steps(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
) -> list[WorkflowRunStep]:
    """Return all run steps for a workflow in deterministic step order."""
    result = await db.execute(
        select(WorkflowRunStep)
        .where(WorkflowRunStep.workflow_id == workflow_id)
        .order_by(WorkflowRunStep.step_order.asc(), WorkflowRunStep.created_at.asc())
    )
    return list(result.scalars().all())


async def mark_run_started(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
) -> None:
    """Stamp ``started_at`` + ``status='active'`` on the workflow row.

    The function intentionally does not move any ``WorkflowRunStep`` rows
    into ``running`` — that belongs to P1.4's scheduler loop. P1.3 only needs
    to advertise that the workflow has begun execution so dashboards stop
    showing it as ``provisioning``.
    """
    workflow = await db.get(ProjectWorkflow, workflow_id)
    if workflow is None:
        logger.warning("[AORunRepo] mark_run_started: workflow {} not found", workflow_id)
        return
    now = datetime.now(UTC)
    workflow.started_at = now
    workflow.status = "active"
    workflow.last_event_at = now
    workflow.updated_at = now
    await db.flush()
    logger.info(
        "[AORunRepo] Marked workflow {} active at {}",
        workflow_id,
        now.isoformat(),
    )


async def mark_step_status(
    db: AsyncSession,
    *,
    step_id: uuid.UUID,
    status: str,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> WorkflowRunStep | None:
    """Stamp ``status`` (and optional timestamps) on a single ``WorkflowRunStep`` row.

    The function is the P2 dispatcher / collector's primary persistence
    hook. It deliberately does **not** validate the transition against
    the migration's ``CheckConstraint`` enum — the dispatcher module is the
    canonical source of allowed transitions, and we want a single error
    surface there. ``None`` is returned when the row is missing so callers
    can raise a domain-specific error (e.g. ``AOIntegrationError``).
    """
    row = await db.get(WorkflowRunStep, step_id)
    if row is None:
        logger.warning("[AORunRepo] mark_step_status: step {} not found", step_id)
        return None
    row.status = status
    if started_at is not None:
        row.started_at = started_at
    if completed_at is not None:
        row.completed_at = completed_at
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return row


async def get_step(
    db: AsyncSession,
    *,
    step_id: uuid.UUID,
) -> WorkflowRunStep | None:
    """Return one ``WorkflowRunStep`` row by id, or ``None`` if missing."""
    return await db.get(WorkflowRunStep, step_id)


async def has_quality_step(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    step_key: str,
) -> bool:
    """Return ``True`` when another step in the workflow has ``role_path`` pointing at the quality slot.

    P2.1 uses this to decide whether ``collect_step_result`` should set the
    completed step to ``quality_checking`` (a downstream quality step will
    re-dispatch it) or ``succeeded`` (terminal). The check is intentionally
    textual because ``role_path`` is the only stable handle the YAML exposes.
    """
    quality_keys = ("quality", "review", "qa")
    key = step_key.lower()
    candidates = [quality_keys[0]] if key != quality_keys[0] else quality_keys[1:]
    if not candidates:
        return False
    result = await db.execute(
        select(WorkflowRunStep.id).where(
            WorkflowRunStep.workflow_id == workflow_id,
            WorkflowRunStep.step_key.in_(tuple(candidates)),
        )
    )
    return result.scalar_one_or_none() is not None