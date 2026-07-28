"""Scheduler tools — the 调度官 Agent's tool surface for P1.4.

The module is the typed contract between the 项目调度官 Agent and the
surrounding Clawith services. Every helper returns a JSON-friendly ``dict``
so the Agent Runtime can serialize outcomes into the tool-calling loop, and
any unexpected error is wrapped in :class:`AOIntegrationError` so the
Runtime can surface a clean recovery message instead of a stack trace.

The tools are deliberately split into three flavours:

* **AO subprocess boundary** (``ao_parse_workflow`` /
  ``ao_get_execution_plan`` / ``ao_resume_from_step``) — thin wrappers around
  :class:`AOClient`. They pass a file-system ``yaml_path`` resolved from
  ``settings.AO_WORKFLOWS_DIR`` so tests can monkeypatch the path without
  spinning up a real AO CLI.
* **File-system helpers** (``init_workflow_dir`` /
  ``update_workflow_status``) — create the ``ao-output/<workflow_id>/`` asset
  scaffold so AO and the Runtime agree on directory layout (see
  ``需求.md`` §4.7).  ``update_workflow_status`` also writes a small JSON
  audit log used by ``scheduler_kickoff``.
* **Clawith business services** (``dispatch_task_to_role`` /
  ``send_channel_message``) — reach into the existing group message
  pipeline. They rely on a per-call ``scheduler_tool_context`` (set by the
  Runtime) which carries the live ``AsyncSession`` plus the current
  ``Agent`` / ``User`` identifiers so the same tool module can be invoked
  by the WebSocket runtime, the new ``/api/ao/*`` REST endpoints, or tests
  that supply a fake scope.

The ``update_project_status`` / ``trigger_approval_node`` /
``audit_skill_application`` helpers are explicit P2 / P2.4 stubs that return
deterministic ``{ok: True, stub: True, ...}`` shapes. This lets P1.4 ship
the complete surface area required by ``需求.md`` §4.1 without faking any
business behaviour: any future wiring only has to replace the stub body.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.services import group_message_service
from app.services.ao.client import AOClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _ao_workflows_dir() -> Path:
    """Resolve the workflows directory from the live settings.

    The function is called every time so tests can override
    ``get_settings`` (a module-level reference) without re-importing the
    module.  Fallback mirrors :class:`AOClient` behaviour.
    """
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


class AOIntegrationError(RuntimeError):
    """A scheduler tool failed in a way the Runtime should surface to the model."""


@dataclass(frozen=True)
class SchedulerToolContext:
    """Per-call context the Runtime injects so helpers can stay pure functions.

    ``workflow_id`` is required for tools that target a project workflow
    (``dispatch_task_to_role``); ``actor_agent_id`` / ``user_id`` propagate
    the caller's identity so the group message pipeline can resolve a
    ``sender_participant_id`` without the tool having to look it up.
    """

    db: AsyncSession
    workflow_id: uuid.UUID | None = None
    actor_agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


_scheduler_ctx: ContextVar[SchedulerToolContext | None] = ContextVar(
    "scheduler_tool_context", default=None
)


@contextlib.contextmanager
def scheduler_tool_context(
    *,
    db: AsyncSession,
    workflow_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
):
    """Push a :class:`SchedulerToolContext` for the duration of a ``with`` block.

    The Runtime sets this once per scheduler tool call so the helpers
    (which are plain functions on the agent tool side) can stay pure.
    Tests use the same context manager to inject a fake session.
    """
    ctx = SchedulerToolContext(
        db=db,
        workflow_id=workflow_id,
        actor_agent_id=actor_agent_id,
        user_id=user_id,
    )
    token = _scheduler_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _scheduler_ctx.reset(token)


def _current_context() -> SchedulerToolContext:
    ctx = _scheduler_ctx.get()
    if ctx is None:
        raise AOIntegrationError(
            "Scheduler tool called without scheduler_tool_context; "
            "wrap the call in app.services.ao.scheduler_tools.scheduler_tool_context."
        )
    return ctx


def _wrap(tool_name: str, fn, *args, **kwargs):
    """Invoke a tool body and re-raise any failure as ``AOIntegrationError``."""
    try:
        return fn(*args, **kwargs)
    except AOIntegrationError:
        raise
    except Exception as exc:
        logger.exception("[SchedulerTools] {} failed", tool_name)
        raise AOIntegrationError(f"{tool_name} failed: {exc}") from exc


def _workflow_yaml_path(workflow_id: str) -> Path:
    """Return ``<AO_WORKFLOWS_DIR>/<workflow_id>.yaml`` and create the parent."""
    base = _ao_workflows_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{workflow_id}.yaml"


def _workflow_run_dir(workflow_id: str) -> Path:
    """Return the per-workflow ``ao-output/<workflow_id>`` directory."""
    base = _ao_output_dir()
    run_dir = base / workflow_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _read_yaml(workflow_id: str) -> str:
    path = _workflow_yaml_path(workflow_id)
    if not path.is_file():
        raise AOIntegrationError(
            f"Workflow YAML not found for {workflow_id}; "
            "ensure P1.3 has composed the workflow before calling scheduler tools."
        )
    return path.read_text(encoding="utf-8")


def _step_to_dict(step) -> dict[str, Any]:
    """Return a JSON-friendly view of an AO ``StepPlan`` or Pydantic-like step."""
    if hasattr(step, "model_dump"):
        return step.model_dump()
    if isinstance(step, dict):
        return dict(step)
    return dict(vars(step))


# ─────────────────────────────────────────────────────────────────────────────
# AO subprocess boundary
# ─────────────────────────────────────────────────────────────────────────────


def ao_parse_workflow(workflow_id: str) -> dict:
    """Validate then parse the AO YAML referenced by ``workflow_id``.

    Returns ``{ok, steps_count}`` so the scheduler knows whether the plan
    is still legible.  ``steps_count`` mirrors the number of AO ``Step``
    objects the CLI discovered — it is the canonical signal the Runtime
    uses to decide whether the kickoff plan should run.
    """
    def _body() -> dict:
        path = _workflow_yaml_path(workflow_id)
        client = AOClient()
        validation = client.validate(path)
        if not validation.ok:
            return {"ok": False, "steps_count": 0, "error": validation.stderr.strip() or "invalid"}
        parsed = client.parse_workflow(path.read_text(encoding="utf-8"))
        return {"ok": True, "steps_count": len(parsed.steps)}

    return _wrap("ao_parse_workflow", _body)


def ao_get_execution_plan(workflow_id: str) -> list[dict]:
    """Return the AO execution plan as a JSON-serialisable ``[step_dict, ...]``."""
    def _body() -> list[dict]:
        path = _workflow_yaml_path(workflow_id)
        client = AOClient()
        plans = client.plan(path)
        return [_step_to_dict(step) for step in plans]

    return _wrap("ao_get_execution_plan", _body)


def ao_resume_from_step(
    workflow_id: str,
    from_step: str,
    feedback: str | None = None,
) -> dict:
    """Resume a workflow from a checkpoint with optional reviewer feedback."""
    def _body() -> dict:
        path = _workflow_yaml_path(workflow_id)
        client = AOClient()
        result = client.resume_from_step(path, from_step=from_step, feedback=feedback)
        return {
            "returncode": result.returncode,
            "output_dir": str(result.output_dir) if getattr(result, "output_dir", None) is not None else None,
        }

    return _wrap("ao_resume_from_step", _body)


# ─────────────────────────────────────────────────────────────────────────────
# File-system helpers
# ─────────────────────────────────────────────────────────────────────────────


_STAGE_DIRECTORIES: tuple[tuple[str, str], ...] = (
    ("00-需求", "需求基线、原始 brief 与 HR 决策记录"),
    ("01-执行", "各执行角色的步骤产物（占位由运行期写入）"),
    ("02-质控", "质量评审官的评分、整改记录与最终报告"),
    ("03-交付", "交付协调官汇总的交付包、验收申请与历史"),
)


def init_workflow_dir(workflow_id: str) -> dict:
    """Create the four-stage asset directory scaffold and a top-level README."""
    run_dir = _workflow_run_dir(workflow_id)
    readme = run_dir / "README.md"
    readme_lines = [
        f"# Workflow {workflow_id}",
        "",
        "本目录由调度官首发触发器自动初始化，目录结构与需求 §4.7 对齐。",
        "",
        "| 子目录 | 用途 |",
        "|--------|------|",
    ]
    for name, description in _STAGE_DIRECTORIES:
        subdir = run_dir / name
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "README.md").write_text(
            f"# {name}\n\n{description}\n",
            encoding="utf-8",
        )
        readme_lines.append(f"| {name}/ | {description} |")
    readme.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "run_dir": str(run_dir),
    }


def update_workflow_status(
    workflow_id: str,
    status: str,
    *,
    note: str | None = None,
) -> dict:
    """Append a status entry to ``workflow.status`` so dashboards stay in sync.

    The function writes to a small JSON audit file under the workflow
    output directory. It is intentionally separate from the database
    ``ProjectWorkflow.status`` update performed by
    ``run_repository.mark_run_started`` — the latter is the source of
    truth for state machine transitions; this file is the source of truth
    for per-event audit trail.
    """
    run_dir = _workflow_run_dir(workflow_id)
    status_file = run_dir / "workflow.status"
    now = datetime.now(UTC)
    payload = {
        "status": status,
        "note": note,
        "last_event_at": now.isoformat(),
    }
    status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "workflow_id": workflow_id, **payload}


# ─────────────────────────────────────────────────────────────────────────────
# Clawith business services
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _DispatchScope:
    """Resolution result of a scheduler tool call into a project workflow."""

    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    group_id: uuid.UUID
    session_id: uuid.UUID
    scheduler_agent_id: uuid.UUID
    creator_id: uuid.UUID
    sender_participant_id: uuid.UUID
    target_participant_id: uuid.UUID


async def _load_dispatch_scope(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    target_agent_id: uuid.UUID,
) -> _DispatchScope:
    """Resolve every FK the dispatcher needs in a single round-trip.

    The dispatcher cannot afford an extra query per field, so we read the
    workflow, the executor Agent, and the scheduler Agent participants
    together. ``target_participant_id`` is the executor's participant id;
    ``sender_participant_id`` is the scheduler (group leader) — the
    existing group message service routes assistant→owner mentions
    automatically, but we still need a sender identity for audit.
    """
    from app.models.agent import Agent
    from app.models.participant import Participant
    from app.models.project import ProjectWorkflow

    workflow = await db.scalar(
        select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id)
    )
    if workflow is None or workflow.group_id is None or workflow.session_id is None:
        raise AOIntegrationError(
            f"Workflow {workflow_id} is not ready to dispatch tasks: missing group/session."
        )
    if workflow.group_leader_agent_id is None:
        raise AOIntegrationError(
            f"Workflow {workflow_id} is missing its group leader (scheduler)."
        )
    participants = {
        participant.ref_id: participant
        for participant in (
            await db.execute(
                select(Participant).where(
                    Participant.ref_id.in_(
                        [workflow.group_leader_agent_id, target_agent_id]
                    )
                )
            )
        )
        .scalars()
        .all()
    }
    scheduler_participant = participants.get(workflow.group_leader_agent_id)
    target_participant = participants.get(target_agent_id)
    if scheduler_participant is None or target_participant is None:
        raise AOIntegrationError(
            f"Workflow {workflow_id} participants are incomplete for dispatch."
        )
    target_agent = await db.get(Agent, target_agent_id)
    if target_agent is None:
        raise AOIntegrationError(f"Target Agent {target_agent_id} not found for dispatch.")
    creator = workflow.creator_id
    if creator is None:
        raise AOIntegrationError(f"Workflow {workflow_id} has no recorded creator.")
    return _DispatchScope(
        tenant_id=workflow.tenant_id,
        workflow_id=workflow_id,
        group_id=workflow.group_id,
        session_id=workflow.session_id,
        scheduler_agent_id=workflow.group_leader_agent_id,
        creator_id=creator,
        sender_participant_id=scheduler_participant.id,
        target_participant_id=target_participant.id,
    )


async def dispatch_task_to_role(
    role_agent_id: str,
    task_summary: str,
    inputs: dict | None = None,
    *,
    expected_outputs: list[str] | None = None,
    step_id: str | None = None,
) -> dict:
    """Persist a DAG step dispatch, queue the work, and ping the target Agent.

    The function is the runtime's "调度官 → 执行位" handoff. Behaviour:

    1. Resolve the workflow scope (group / session / scheduler Agent) via
       :func:`_load_dispatch_scope` so the same call works whether the
       caller is the WebSocket Runtime or a test.
    2. Stage or update a ``WorkflowRunStep`` row:
       * ``step_id`` provided → ``UPDATE status='running', started_at=now``;
       * ``step_id`` not provided → ``INSERT`` a fresh row with
         ``status='pending'`` so the dispatch loop can pick it up next
         round. Either way the row carries the optional ``expected_outputs``
         and ``input_refs`` so the quality step can read them back.
    3. Create a durable ``Task`` row so the existing Runtime can route the
       mention through ``enqueue_group_message``.  We deliberately keep
       ``project_task_dispatch=False`` so we do not re-enter the user-driven
       project Task DAG.
    4. ``init_workflow_dir`` so ``ao-output/<workflow>/`` scaffold exists
       even if the kickoff has not run yet.

    Returns a JSON-friendly dict including the resolved ``step_id`` so the
    caller can later call :func:`collect_step_result` without re-fetching
    the step row. Errors are wrapped in :class:`AOIntegrationError`.
    """
    ctx = _current_context()
    if ctx.workflow_id is None:
        raise AOIntegrationError("dispatch_task_to_role requires scheduler_tool_context(workflow_id=...)")
    if not task_summary.strip():
        raise AOIntegrationError("dispatch_task_to_role requires a non-empty task_summary.")

    try:
        target_agent_uuid = uuid.UUID(str(role_agent_id))
    except ValueError as exc:
        raise AOIntegrationError(f"role_agent_id must be a UUID: {exc}") from exc

    scope = _load_dispatch_scope(
        ctx.db, workflow_id=ctx.workflow_id, target_agent_id=target_agent_uuid
    )
    if inspect.isawaitable(scope):
        scope = await scope

    from app.models.task import Task
    from app.models.workflow_run import WorkflowRunStep

    step_row_id: uuid.UUID
    resolved_step_key: str | None
    if step_id:
        try:
            step_uuid = uuid.UUID(str(step_id))
        except ValueError as exc:
            raise AOIntegrationError(f"step_id must be a UUID: {exc}") from exc
        step_row = await ctx.db.get(WorkflowRunStep, step_uuid)
        if step_row is None:
            raise AOIntegrationError(
                f"dispatch_task_to_role: WorkflowRunStep {step_id} not found."
            )
        if step_row.workflow_id != scope.workflow_id:
            raise AOIntegrationError(
                f"dispatch_task_to_role: step {step_id} does not belong to workflow {scope.workflow_id}."
            )
        step_row.agent_id = target_agent_uuid
        step_row.task_summary = task_summary.strip()
        if expected_outputs:
            step_row.acceptance_text = json.dumps(list(expected_outputs), ensure_ascii=False)
        if inputs is not None:
            step_row.input_refs = dict(inputs)
        step_row.status = "running"
        step_row.started_at = datetime.now(UTC)
        step_row_id = step_row.id
        resolved_step_key = step_row.step_key
    else:
        step_row = WorkflowRunStep(
            id=uuid.uuid4(),
            tenant_id=scope.tenant_id,
            workflow_id=scope.workflow_id,
            step_key=f"ad_hoc_{uuid.uuid4().hex[:8]}",
            step_order=10_000,
            role_path="product/ad-hoc-dispatch",
            agent_id=target_agent_uuid,
            task_summary=task_summary.strip(),
            input_refs=dict(inputs) if inputs else None,
            acceptance_text=json.dumps(list(expected_outputs), ensure_ascii=False)
            if expected_outputs
            else None,
            depends_on=[],
            status="pending",
        )
        ctx.db.add(step_row)
        await ctx.db.flush()
        step_row_id = step_row.id
        resolved_step_key = step_row.step_key

    task = Task(
        id=uuid.uuid4(),
        agent_id=target_agent_uuid,
        title=task_summary.strip()[:200],
        description=json.dumps(inputs or {}, ensure_ascii=False),
        type="todo",
        status="pending",
        priority="high",
        created_by=scope.creator_id,
        project_workflow_id=scope.workflow_id,
        group_id=scope.group_id,
        session_id=scope.session_id,
        dependency_task_ids=[],
        report_to_agent_id=scope.scheduler_agent_id,
        is_project_closure=False,
    )
    ctx.db.add(task)
    await ctx.db.flush()

    rendered_inputs = ""
    if inputs:
        rendered_inputs = "\n\n输入：\n" + json.dumps(inputs, ensure_ascii=False, indent=2)
    rendered_outputs = ""
    if expected_outputs:
        rendered_outputs = "\n\n期望产出：\n" + "\n".join(f"- {item}" for item in expected_outputs)
    content = (
        f"【调度分发】{task_summary.strip()}{rendered_inputs}{rendered_outputs}\n\n"
        f"step_id: {step_row_id}\n"
        "请按依赖与执行规范完成本任务，完成后结果会自动向调度官回报。"
    )
    init_result = init_workflow_dir(str(scope.workflow_id))
    intake = await group_message_service.enqueue_group_message(
        ctx.db,
        tenant_id=scope.tenant_id,
        group_id=scope.group_id,
        session_id=scope.session_id,
        sender_participant_id=scope.sender_participant_id,
        content=content,
        mention_participant_ids=[scope.target_participant_id],
        message_id=uuid.uuid4(),
        project_task_dispatch=False,
    )

    # P2.3 light hook: refresh the ``01-执行/README.md`` so the group
    # workspace always reflects who is currently on the bench for this
    # step.  Failure is non-fatal — dispatching the task matters more
    # than the audit row.
    try:
        from app.services.ao.asset_writer import write_readme

        readme_body = (
            f"# 执行位\n\n"
            f"本步由 {target_agent_uuid} 负责。\n\n"
            f"- 任务摘要：{task_summary.strip()[:200]}\n"
            f"- 任务 ID：{task.id}\n"
            f"- 步骤 ID：{step_row_id}\n"
            f"- 调度时间：{datetime.now(UTC).isoformat()}\n"
        )
        await write_readme(
            ctx.db,
            workflow_id=ctx.workflow_id,
            tenant_id=scope.tenant_id,
            category="execution",
            body=readme_body,
            step_id=step_row_id,
        )
    except Exception as exc:  # noqa: BLE001 — README hook is best-effort
        logger.warning(
            "[SchedulerTools] execution README hook failed for {}: {}",
            ctx.workflow_id,
            exc,
        )

    return {
        "ok": True,
        "task_id": str(task.id),
        "step_id": str(step_row_id),
        "step_key": resolved_step_key,
        "group_id": str(scope.group_id),
        "message_id": str(intake.message.id),
        "dispatch_kind": intake.dispatch_kind,
        "run_dir": init_result.get("run_dir"),
    }


@dataclass(frozen=True)
class _GroupScope:
    tenant_id: uuid.UUID
    group_id: uuid.UUID
    session_id: uuid.UUID
    sender_participant_id: uuid.UUID


async def _load_group_scope(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    actor_agent_id: uuid.UUID | None,
) -> _GroupScope:
    """Resolve the sender / session for a ``send_channel_message`` call.

    Without an ``actor_agent_id`` we cannot pick a deterministic
    sender, so we fall back to the group owner Agent. ``session_id`` is
    the active primary session of the group.
    """
    from app.models.chat_session import ChatSession
    from app.models.group import Group
    from app.models.participant import Participant

    group = await db.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        raise AOIntegrationError(f"Group {group_id} not found or deleted.")

    scheduler_agent_id = actor_agent_id or group.owner_agent_id
    if scheduler_agent_id is None:
        raise AOIntegrationError(
            f"Cannot determine scheduler Agent for group {group_id}: "
            "neither caller nor group owner is set."
        )
    sender = await db.scalar(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id == scheduler_agent_id,
        )
    )
    if sender is None:
        raise AOIntegrationError(
            f"Scheduler Agent {scheduler_agent_id} has no Participant row; cannot send."
        )
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.group_id == group_id,
            ChatSession.deleted_at.is_(None),
        ).order_by(ChatSession.created_at.asc())
    )
    if session is None:
        raise AOIntegrationError(f"Group {group_id} has no active chat session.")
    return _GroupScope(
        tenant_id=group.tenant_id,
        group_id=group_id,
        session_id=session.id,
        sender_participant_id=sender.id,
    )


async def send_channel_message(group_id: str, content: str) -> dict:
    """Send a public message into the project execution group as the scheduler.

    P1.4 uses this to publish "首发开跑" / progress broadcast messages. The
    helper always disables ``project_task_dispatch`` because broadcasts are
    not user-triggered work — they are status updates the leader reads back
    as a normal group message.
    """
    if not content.strip():
        raise AOIntegrationError("send_channel_message requires non-empty content.")
    try:
        group_uuid = uuid.UUID(str(group_id))
    except ValueError as exc:
        raise AOIntegrationError(f"group_id must be a UUID: {exc}") from exc

    ctx = _current_context()
    scope = _load_group_scope(
        ctx.db, group_id=group_uuid, actor_agent_id=ctx.actor_agent_id
    )
    if inspect.isawaitable(scope):
        scope = await scope
    intake = await group_message_service.enqueue_group_message(
        ctx.db,
        tenant_id=scope.tenant_id,
        group_id=scope.group_id,
        session_id=scope.session_id,
        sender_participant_id=scope.sender_participant_id,
        content=content,
        mention_participant_ids=None,
        message_id=uuid.uuid4(),
        project_task_dispatch=False,
    )
    return {
        "ok": True,
        "group_id": str(scope.group_id),
        "message_id": str(intake.message.id),
        "dispatch_kind": intake.dispatch_kind,
    }


# ─────────────────────────────────────────────────────────────────────────────
# P2 / P2.4 stubs (deferred wiring — see 需求.md §4.1)
# ─────────────────────────────────────────────────────────────────────────────


def update_project_status(workflow_id: str, status: str) -> dict:
    """P2 stub: surface a workflow-level status update to upstream dashboards.

    The real implementation will be added in P2 once the project-level
    metrics pipeline exists; for now we keep the call signature stable so
    P1.4 can ship the full scheduler tool registry.
    """
    del workflow_id  # reserved for the P2 wiring
    return {"ok": True, "stub": True, "status": status}


async def trigger_approval_node(
    workflow_id: str,
    step_id: str,
    prompt: str,
    approver_user_ids: list[str] | None = None,
) -> dict:
    """P2.4 real: request human approval for a workflow step.

    Upgraded from a P1.4 stub: the function now delegates to
    :mod:`app.services.ao.approval_node`, which writes a
    ``ProjectDecision`` row, flips the matching ``WorkflowRunStep`` to
    ``awaiting_approval``, and broadcasts a card into the execution
    group with a ``<!--approval:<decision_id>-->`` marker the frontend
    can render.

    ``approver_user_ids`` is optional for backward compatibility with
    the stub shape; when omitted the tool raises so callers explicitly
    pick human approvers. ``step_id`` accepts either the
    ``WorkflowRunStep.id`` UUID or its ``step_key`` (e.g. ``"execute"``)
    — the implementation normalises to the row id when possible.
    """
    if not step_id.strip():
        raise AOIntegrationError("trigger_approval_node requires a non-empty step_id.")
    ctx = _current_context()

    from app.services.ao import approval_node

    workflow_uuid = uuid.UUID(workflow_id)
    step_uuid = await _resolve_step_id(ctx.db, workflow_id=workflow_uuid, step_ref=step_id)
    parsed_approvers: list[uuid.UUID] = []
    for raw in approver_user_ids or []:
        try:
            parsed_approvers.append(uuid.UUID(str(raw)))
        except ValueError as exc:
            raise AOIntegrationError(
                f"approver_user_ids must be UUIDs: {exc}"
            ) from exc
    if not parsed_approvers:
        raise AOIntegrationError(
            "trigger_approval_node requires at least one approver_user_id."
        )

    try:
        return await approval_node.trigger_approval_node(
            ctx.db,
            workflow_id=workflow_uuid,
            step_id=step_uuid,
            prompt=prompt,
            approver_user_ids=parsed_approvers,
        )
    except AOIntegrationError:
        raise
    except Exception as exc:
        logger.exception("[SchedulerTools] trigger_approval_node failed for {}", workflow_id)
        raise AOIntegrationError(f"trigger_approval_node failed: {exc}") from exc


async def _resolve_step_id(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    step_ref: str,
) -> uuid.UUID:
    """Translate a ``step_id`` argument to a ``WorkflowRunStep.id`` UUID.

    Accepts a row UUID directly, or a ``step_key`` (e.g. ``"review"``)
    that exists in the workflow's run steps. Falls back to the raw
    string when no match is found so the downstream
    :mod:`approval_node` layer can raise a clearer error message.
    """
    from app.models.workflow_run import WorkflowRunStep

    try:
        return uuid.UUID(step_ref)
    except ValueError:
        pass

    result = await db.execute(
        select(WorkflowRunStep.id).where(
            WorkflowRunStep.workflow_id == workflow_id,
            WorkflowRunStep.step_key == step_ref,
        )
    )
    row_id = result.scalar_one_or_none() if result is not None else None
    if row_id is not None:
        return row_id
    raise AOIntegrationError(
        f"trigger_approval_node: step '{step_ref}' is not a UUID and no matching "
        f"step_key was found in workflow {workflow_id}."
    )


def audit_skill_application(workflow_id: str, skill_id: str, level: str) -> dict:
    """P2 stub: record that a skill application was reviewed by the scheduler.

    P2 skills learning will replace the body; the contract is locked so
    callers can already use the tool surface.
    """
    del workflow_id  # reserved for P2
    if not skill_id.strip():
        raise AOIntegrationError("audit_skill_application requires a non-empty skill_id.")
    return {"ok": True, "stub": True, "skill_id": skill_id, "level": level}
