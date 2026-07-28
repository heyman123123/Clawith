"""Human-approval node for AO workflow steps (P2.4).

This module upgrades the P1.4 ``trigger_approval_node`` stub into a real
implementation that bridges an AO step that needs a human sign-off with
Clawith's existing ``ProjectDecision`` + group-message pipeline. The flow
mirrors how P1.4 already uses ``ProjectDecision`` for ad-hoc user prompts
but pins the wiring to the workflow + step row so the runtime can resume
from the same step on approval and the audit trail is durable.

Contract summary (per ``需求.md`` §3.2 stage 4 and §4.1):

* ``trigger_approval_node`` writes a ``ProjectDecision`` row in
  ``status='pending'``, flips the matching ``WorkflowRunStep`` to
  ``status='awaiting_approval'``, and broadcasts a group message with the
  ``<!--approval:<decision_id>-->`` marker so the frontend card can
  resolve it. The message mentions the human approvers.
* ``resolve_approval`` writes the user response, then either
  ``ao_resume_from_step`` (P1.1) for approval or marks the step
  ``status='failed'`` and posts a rejection notice for rejection.
  ``ProjectDecision.status`` is bumped to ``answered``.

Failures are surfaced as :class:`AOIntegrationError` so the Runtime can
emit a single recovery line in the group instead of leaking a stack
trace. The module never shells out to AO itself — it delegates to
``scheduler_tools.ao_resume_from_step`` (which the tests monkeypatch).
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.participant import Participant
from app.models.project import ProjectDecision, ProjectWorkflow
from app.models.workflow_run import WorkflowRunStep
from app.services import group_message_service
from app.services.ao.scheduler_tools import (
    AOIntegrationError,
    ao_resume_from_step,
)
from app.services.group_message_service import GroupMessageServiceError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Group-message marker used by the frontend to render the approval card.
# Kept as an HTML comment so the raw text remains readable in any IM bridge.
_APPROVAL_MARKER_TEMPLATE = "<!--approval:{decision_id}-->"

# Truncate very long prompts so the broadcast stays scannable in chat.
_PROMPT_SUMMARY_LIMIT = 240


def _build_decision_context(
    *,
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
    prompt: str,
    approver_user_ids: list[uuid.UUID],
    requesting_agent_id: uuid.UUID | None,
) -> str:
    """Encode the full approval context as JSON inside ``ProjectDecision.context``.

    ``ProjectDecision.context`` is a free-form ``Text`` column; we keep the
    raw ``prompt`` plus the structured refs so the API endpoint can rebuild
    the timeline without re-querying the step row.
    """
    payload = {
        "workflow_id": str(workflow_id),
        "step_id": str(step_id),
        "prompt": prompt,
        "approver_user_ids": [str(value) for value in approver_user_ids],
    }
    if requesting_agent_id is not None:
        payload["requesting_agent_id"] = str(requesting_agent_id)
    return json.dumps(payload, ensure_ascii=False)


def _summarize_prompt(prompt: str) -> str:
    """Return a single-line summary of ``prompt`` safe to embed in a broadcast."""
    normalized = " ".join(prompt.split())
    if len(normalized) <= _PROMPT_SUMMARY_LIMIT:
        return normalized
    return normalized[: _PROMPT_SUMMARY_LIMIT - 1] + "…"


async def _load_step(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
) -> WorkflowRunStep:
    """Return the run step for ``(workflow_id, step_id)`` or raise."""
    step = await db.scalar(
        select(WorkflowRunStep).where(
            WorkflowRunStep.workflow_id == workflow_id,
            WorkflowRunStep.id == step_id,
        )
    )
    if step is None:
        raise AOIntegrationError(f"Approval step {step_id} not found for workflow {workflow_id}.")
    return step


async def _resolve_active_session_id(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the first active ``ChatSession`` id for the group, or ``None``."""
    return await db.scalar(
        select(ChatSession.id)
        .where(ChatSession.group_id == group_id, ChatSession.deleted_at.is_(None))
        .order_by(ChatSession.created_at.asc())
    )


async def _load_sender_participant(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
) -> uuid.UUID:
    """Return the participant id of the scheduler Agent, used as message sender.

    Falls back to the group owner Agent when ``scheduler_agent_id`` is not
    set yet. We intentionally bypass ``scheduler_tools._load_group_scope``
    because that helper also validates the active chat session — for an
    approval broadcast we already know the workflow has both a group and
    a session, and we want the broadcast to be cheap to issue.
    """
    scheduler_id = getattr(workflow, "scheduler_agent_id", None) or getattr(workflow, "group_leader_agent_id", None)
    if scheduler_id is None:
        raise AOIntegrationError(f"Workflow {workflow.id} has no scheduler Agent; cannot send approval broadcast.")
    participant = await db.scalar(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id == scheduler_id,
        )
    )
    if participant is None:
        # Fallback: resolve via the Agent row's own user_id, if any. The
        # scheduler is normally registered as a participant by
        # ``provision_team_from_plan``; this branch is a safety net.
        agent = await db.get(Agent, scheduler_id)
        if agent is None:
            raise AOIntegrationError(f"Scheduler Agent {scheduler_id} has no Participant row; cannot send.")
        raise AOIntegrationError(
            f"Scheduler Agent {scheduler_id} ({agent.name}) has no Participant row; "
            "ensure the workflow was provisioned via provision_team_from_plan."
        )
    return participant.id


async def _load_approver_participants(
    db: AsyncSession,
    *,
    approver_user_ids: list[uuid.UUID],
) -> list[Participant]:
    """Resolve User ids to their Participant rows so we can @-mention them."""
    if not approver_user_ids:
        return []
    result = await db.execute(
        select(Participant).where(
            Participant.type == "user",
            Participant.ref_id.in_(approver_user_ids),
        )
    )
    participants = list(result.scalars().all())
    found_user_ids = {participant.ref_id for participant in participants}
    missing = [user_id for user_id in approver_user_ids if user_id not in found_user_ids]
    if missing:
        raise AOIntegrationError(f"Approver users are not registered as participants: {missing}")
    return participants


async def trigger_approval_node(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
    prompt: str,
    approver_user_ids: list[uuid.UUID],
) -> dict:
    """Create a pending decision + awaiting_approval step + group broadcast.

    The function reuses the existing ``ProjectDecision`` and
    ``enqueue_group_message`` pipeline so the group timeline stays
    single-source-of-truth. The decision ``context`` is JSON so the API
    can reconstruct the relationship without joining extra tables.
    """
    if not prompt.strip():
        raise AOIntegrationError("trigger_approval_node requires a non-empty prompt.")
    if not approver_user_ids:
        raise AOIntegrationError("trigger_approval_node requires at least one approver_user_id.")

    workflow = await db.scalar(select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id))
    if workflow is None:
        raise AOIntegrationError(f"Workflow {workflow_id} not found for approval.")
    if workflow.group_id is None:
        raise AOIntegrationError(f"Workflow {workflow_id} is not ready for approval: missing group.")
    session_id = await _resolve_active_session_id(db, group_id=workflow.group_id)
    if session_id is None:
        raise AOIntegrationError(f"Workflow {workflow_id} is not ready for approval: missing active chat session.")

    step = await _load_step(db, workflow_id=workflow_id, step_id=step_id)
    now = datetime.now(UTC)

    requesting_agent_id = getattr(workflow, "scheduler_agent_id", None)
    decision = ProjectDecision(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        group_id=workflow.group_id,
        review_group_id=workflow.decision_group_id,
        session_id=session_id,
        task_id=None,
        requesting_agent_id=requesting_agent_id,
        title=f"审批请求 · {step.step_key}",
        context=_build_decision_context(
            workflow_id=workflow_id,
            step_id=step_id,
            prompt=prompt,
            approver_user_ids=approver_user_ids,
            requesting_agent_id=requesting_agent_id,
        ),
        status="pending",
        response=None,
        responded_at=None,
    )
    db.add(decision)
    await db.flush()

    step.status = "awaiting_approval"
    step.started_at = step.started_at or now
    step.updated_at = now
    await db.flush()

    approver_participants = await _load_approver_participants(db, approver_user_ids=approver_user_ids)
    mention_ids = [participant.id for participant in approver_participants]
    sender_participant_id = await _load_sender_participant(db, workflow=workflow)

    summary = _summarize_prompt(prompt)
    marker = _APPROVAL_MARKER_TEMPLATE.format(decision_id=decision.id)
    content = (
        f"【审批卡】调度官请求人工审批 step={step.step_key}\n"
        f"{summary}\n\n"
        f"审批人：{' '.join('@' + str(pid) for pid in mention_ids) or '无'}\n"
        f"{marker}"
    )
    try:
        intake = await group_message_service.enqueue_group_message(
            db,
            tenant_id=workflow.tenant_id,
            group_id=workflow.group_id,
            session_id=session_id,
            sender_participant_id=sender_participant_id,
            content=content,
            mention_participant_ids=mention_ids or None,
            message_id=uuid.uuid4(),
            project_task_dispatch=False,
        )
    except GroupMessageServiceError as exc:
        raise AOIntegrationError(f"Failed to enqueue approval broadcast for decision {decision.id}: {exc}") from exc

    logger.info(
        "[ApprovalNode] Pending decision {} for workflow {} step {} (approvers={})",
        decision.id,
        workflow_id,
        step_id,
        len(approver_user_ids),
    )
    return {
        "ok": True,
        "decision_id": str(decision.id),
        "step_id": str(step_id),
        "group_id": str(workflow.group_id),
        "group_message_id": str(intake.message.id),
        "approver_user_ids": [str(value) for value in approver_user_ids],
    }


def _parse_decision_context(decision: ProjectDecision) -> dict:
    """Return the structured context for a decision row (best-effort)."""
    try:
        return json.loads(decision.context)
    except (TypeError, ValueError):
        return {}


async def resolve_approval(
    db: AsyncSession,
    *,
    decision_id: uuid.UUID,
    response_text: str,
    approved: bool,
) -> dict:
    """Record a user response and either resume the AO step or fail it."""
    decision = await db.get(ProjectDecision, decision_id)
    if decision is None:
        raise AOIntegrationError(f"Decision {decision_id} not found.")
    if decision.status != "pending":
        raise AOIntegrationError(f"Decision {decision_id} is already resolved (status={decision.status}).")

    context = _parse_decision_context(decision)
    step_id_str = context.get("step_id")
    if not step_id_str:
        raise AOIntegrationError(f"Decision {decision_id} is missing a step_id context; cannot resume.")
    try:
        step_id = uuid.UUID(step_id_str)
    except ValueError as exc:
        raise AOIntegrationError(f"Decision {decision_id} has an invalid step_id: {step_id_str}") from exc

    step = await _load_step(db, workflow_id=decision.workflow_id, step_id=step_id)
    workflow = await db.get(ProjectWorkflow, decision.workflow_id)
    if workflow is None or workflow.group_id is None:
        raise AOIntegrationError(f"Workflow {decision.workflow_id} is not ready to resolve approval.")
    session_id = await _resolve_active_session_id(db, group_id=workflow.group_id)
    if session_id is None:
        raise AOIntegrationError(
            f"Workflow {decision.workflow_id} is not ready to resolve approval: no active chat session."
        )
    setattr(workflow, "_active_session_id", session_id)  # noqa: B010 — runtime-only hint

    now = datetime.now(UTC)
    decision.status = "answered"
    decision.response = response_text
    decision.responded_at = now

    if approved:
        step.status = "running"
        step.updated_at = now
        await db.flush()
        try:
            resume_coro = ao_resume_from_step(
                str(decision.workflow_id),
                str(step.step_key),
                feedback=response_text or None,
            )
            if inspect.isawaitable(resume_coro):
                resume_result = await resume_coro
            else:
                resume_result = resume_coro
        except AOIntegrationError:
            raise
        except Exception as exc:
            raise AOIntegrationError(f"ao_resume_from_step failed for decision {decision_id}: {exc}") from exc
        logger.info(
            "[ApprovalNode] Decision {} approved → step {} resumed (returncode={})",
            decision_id,
            step.step_key,
            resume_result.get("returncode"),
        )
    else:
        step.status = "failed"
        step.quality_feedback = response_text or "审批驳回"
        step.completed_at = now
        step.updated_at = now
        await db.flush()
        try:
            await _broadcast_rejection(
                db,
                workflow=workflow,
                step_key=step.step_key,
                reason=response_text,
            )
        except GroupMessageServiceError as exc:
            raise AOIntegrationError(
                f"Failed to enqueue rejection broadcast for decision {decision_id}: {exc}"
            ) from exc
        logger.info(
            "[ApprovalNode] Decision {} rejected → step {} marked failed",
            decision_id,
            step.step_key,
        )

    return {
        "ok": True,
        "decision_id": str(decision_id),
        "step_id": str(step_id),
        "approved": approved,
        "step_status": step.status,
    }


async def _broadcast_rejection(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    step_key: str,
    reason: str,
) -> None:
    """Post a public rejection notice into the workflow's execution group."""
    sender_participant_id = await _load_sender_participant(db, workflow=workflow)
    session_id = await _resolve_active_session_id(db, group_id=workflow.group_id)  # type: ignore[arg-type]
    if session_id is None:
        raise AOIntegrationError(f"Workflow {workflow.id} has no active chat session; cannot broadcast rejection.")
    summary = _summarize_prompt(reason or "审批驳回")
    content = (
        f"【审批驳回】调度官收到驳回信号，step={step_key} 已标记为失败。\n"
        f"原因：{summary}\n"
        f"请相关角色按驳回意见整改后重新触发审批。"
    )
    await group_message_service.enqueue_group_message(
        db,
        tenant_id=workflow.tenant_id,
        group_id=workflow.group_id,  # type: ignore[arg-type]
        session_id=session_id,
        sender_participant_id=sender_participant_id,
        content=content,
        mention_participant_ids=None,
        message_id=uuid.uuid4(),
        project_task_dispatch=False,
    )


__all__ = [
    "resolve_approval",
    "trigger_approval_node",
]
