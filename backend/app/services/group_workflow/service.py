"""Transactional state transitions for evidence-driven group collaboration."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group, GroupMember
from app.models.group_workflow import GroupWorkflow, GroupWorkflowEvent, GroupWorkflowItem, GroupWorkflowStage
from app.models.participant import Participant
from app.services.group_workflow.contracts import WorkflowPlan
from app.services.group_workflow.templates import preset_workflow

logger = logging.getLogger(__name__)


async def _notify_okr(
    workflow: GroupWorkflow,
    event_key: str,
    stage: GroupWorkflowStage | None = None,
    *,
    confirmed: bool = False,
) -> None:
    """OKR stage arrival is leader/manager confirmation — not auto evidence completion."""
    # Auto stage transitions must not count as "arrived" for OKR push.
    if event_key in {"stage_completed", "workflow_completed", "stage_activated"} and not confirmed:
        return
    try:
        from app.services.okr_workflow_bridge import notify_workflow_event

        await notify_workflow_event(
            tenant_id=workflow.tenant_id,
            group_id=workflow.group_id,
            event_key=event_key,
            workflow_id=workflow.id,
            stage_id=stage.id if stage is not None else None,
        )
    except Exception:
        logger.exception(
            "OKR workflow notify failed workflow=%s event=%s", workflow.id, event_key
        )


async def _workflow_okr_requires_human_confirm(db: AsyncSession, *, tenant_id: uuid.UUID) -> bool:
    """When project-progress OKR push is on, stage completion waits for human confirm."""
    try:
        from app.models.okr import OKRSettings
        from app.services.okr_settings_helpers import workflow_push_active

        settings = await db.scalar(select(OKRSettings).where(OKRSettings.tenant_id == tenant_id))
        return settings is not None and workflow_push_active(settings)
    except Exception:
        logger.exception("Failed to resolve OKR workflow confirm gate for tenant=%s", tenant_id)
        return False


class GroupWorkflowServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkflowTransition:
    workflow: GroupWorkflow
    stage: GroupWorkflowStage
    next_stage: GroupWorkflowStage | None
    leader_action: GroupWorkflowEvent | None


def _now() -> datetime:
    return datetime.now(UTC)


async def _event(
    db: AsyncSession, *, workflow: GroupWorkflow, event_type: str, source: str,
    idempotency_key: str, actor_participant_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None, item_id: uuid.UUID | None = None,
    payload: dict | None = None, dispatch: bool = False,
) -> GroupWorkflowEvent:
    existing = await db.execute(select(GroupWorkflowEvent).where(
        GroupWorkflowEvent.workflow_id == workflow.id,
        GroupWorkflowEvent.idempotency_key == idempotency_key,
    ))
    value = existing.scalar_one_or_none()
    if value is not None:
        return value
    value = GroupWorkflowEvent(
        workflow_id=workflow.id, stage_id=stage_id, item_id=item_id,
        event_type=event_type, actor_participant_id=actor_participant_id,
        source=source, payload=payload or {}, idempotency_key=idempotency_key,
        dispatch_state="pending" if dispatch else "none",
    )
    db.add(value)
    await db.flush()
    return value


async def create_workflow(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID,
    leader_participant_id: uuid.UUID | None, plan: WorkflowPlan,
) -> GroupWorkflow:
    existing = await db.execute(select(GroupWorkflow).where(GroupWorkflow.group_id == group_id).with_for_update())
    workflow = existing.scalar_one_or_none()
    if workflow is not None:
        return workflow
    workflow = GroupWorkflow(
        tenant_id=tenant_id, group_id=group_id, leader_participant_id=leader_participant_id,
        name=plan.name, source=plan.source, status="active",
    )
    db.add(workflow)
    await db.flush()
    stages: list[GroupWorkflowStage] = []
    for position, stage_plan in enumerate(plan.stages):
        stage = GroupWorkflowStage(
            workflow_id=workflow.id, stage_key=stage_plan.key, title=stage_plan.title,
            goal=stage_plan.goal, position=position,
            status="active" if position == 0 else "pending",
            requires_approval=stage_plan.requires_approval,
            acceptance_criteria=stage_plan.acceptance_criteria,
            owner_participant_id=stage_plan.owner_participant_id or leader_participant_id,
            started_at=_now() if position == 0 else None,
        )
        db.add(stage)
        stages.append(stage)
    await db.flush()
    for stage, stage_plan in zip(stages, plan.stages, strict=True):
        for item_plan in stage_plan.items:
            db.add(GroupWorkflowItem(
                workflow_id=workflow.id, stage_id=stage.id, item_key=item_plan.item_key,
                title=item_plan.title, description=item_plan.description,
                assignee_participant_id=item_plan.assignee_participant_id or stage.owner_participant_id,
            ))
    workflow.current_stage_id = stages[0].id
    await _event(db, workflow=workflow, event_type="workflow_created", source="system",
                 idempotency_key="workflow:created", stage_id=stages[0].id,
                 payload={"source": plan.source})
    await _leader_action(db, workflow=workflow, stage=stages[0], kind="stage_activated")
    return workflow


async def create_default_workflow(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    leader_participant_id: uuid.UUID | None,
    goal: str,
) -> GroupWorkflow:
    """Create the always-on default workflow once for a group."""
    return await create_workflow(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        leader_participant_id=leader_participant_id,
        plan=preset_workflow("default", goal=goal.strip() or "推进群协作目标", leader_participant_id=leader_participant_id),
    )


async def get_current(db: AsyncSession, *, group_id: uuid.UUID) -> GroupWorkflow | None:
    result = await db.execute(select(GroupWorkflow).where(GroupWorkflow.group_id == group_id))
    return result.scalar_one_or_none()


async def replace_workflow_from_plan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    leader_participant_id: uuid.UUID | None,
    plan: WorkflowPlan,
    actor_participant_id: uuid.UUID | None = None,
) -> GroupWorkflow:
    """Atomically replace the current lifecycle only after an explicit confirmation."""
    result = await db.execute(
        select(GroupWorkflow).where(GroupWorkflow.group_id == group_id).with_for_update()
    )
    current = result.scalar_one_or_none()
    if current is not None:
        current.status = "completed"
        current.version += 1
        await _event(
            db,
            workflow=current,
            event_type="workflow_replaced",
            source="human",
            actor_participant_id=actor_participant_id,
            idempotency_key=f"workflow:{current.id}:replaced:{current.version}",
        )
        # The schema intentionally enforces one group-scoped workflow. Remove
        # the replaced graph only after recording the replacement transition;
        # the confirmed draft itself remains the durable change record.
        await db.delete(current)
        await db.flush()
    return await create_workflow(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        leader_participant_id=leader_participant_id,
        plan=plan,
    )


async def _human_confirm_targets(db: AsyncSession, *, group_id: uuid.UUID) -> list[dict]:
    """Resolve human managers (fallback: group creator) for proactive @mentions."""
    result = await db.execute(
        select(Participant)
        .join(GroupMember, GroupMember.participant_id == Participant.id)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.role == "manager",
            Participant.type == "user",
        )
        .order_by(Participant.display_name)
    )
    managers = list(result.scalars().all())
    if managers:
        return [
            {"participant_id": str(participant.id), "display_name": participant.display_name}
            for participant in managers
        ]
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if group is None:
        return []
    creator = await db.scalar(select(Participant).where(Participant.id == group.created_by_participant_id))
    if creator is None or creator.type != "user":
        return []
    return [{"participant_id": str(creator.id), "display_name": creator.display_name}]


async def _decision_maker_target(db: AsyncSession, *, group_id: uuid.UUID) -> dict | None:
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if group is None or group.decision_maker_participant_id is None:
        return None
    participant = await db.scalar(
        select(Participant).where(Participant.id == group.decision_maker_participant_id)
    )
    if participant is None or participant.type != "agent":
        return None
    return {"participant_id": str(participant.id), "display_name": participant.display_name}


async def _leader_action(
    db: AsyncSession, *, workflow: GroupWorkflow, stage: GroupWorkflowStage, kind: str,
    item: GroupWorkflowItem | None = None,
) -> GroupWorkflowEvent:
    decision_maker = await _decision_maker_target(db, group_id=workflow.group_id)
    # Stage confirmation is owned by the decision maker when present. Keep human
    # targets only as an override hint for true exceptions / missing DM.
    if kind == "approval_required" and decision_maker is not None:
        confirm_targets: list[dict] = []
    else:
        confirm_targets = await _human_confirm_targets(db, group_id=workflow.group_id)
    payload: dict = {
        "kind": kind,
        "stage_title": stage.title,
        "item_title": item.title if item else None,
        "confirm_targets": confirm_targets,
        "decision_maker": decision_maker,
    }
    return await _event(
        db, workflow=workflow, event_type="leader_action", source="workflow",
        idempotency_key=f"leader:{workflow.version}:{kind}:{stage.id}:{item.id if item else '-'}",
        stage_id=stage.id, item_id=item.id if item else None, dispatch=True,
        payload=payload,
    )


async def _decision_action(
    db: AsyncSession, *, workflow: GroupWorkflow, stage: GroupWorkflowStage, kind: str,
) -> GroupWorkflowEvent | None:
    group = await db.scalar(select(Group).where(Group.id == workflow.group_id))
    if group is None:
        return None
    if group.decision_maker_participant_id is None:
        from app.services.group_decision.seed import ensure_group_decision_maker_from_group

        await ensure_group_decision_maker_from_group(
            db,
            group=group,
            goal=group.description or group.name,
            require_ready=False,
        )
        await db.refresh(group)
    if group.decision_maker_participant_id is None:
        logger.warning(
            "No decision maker for group %s; skipping decision_action wake", workflow.group_id
        )
        return None
    payload = {
        "kind": kind,
        "stage_title": stage.title,
        "stage_id": str(stage.id),
        "decision_maker_participant_id": str(group.decision_maker_participant_id),
    }
    return await _event(
        db,
        workflow=workflow,
        event_type="decision_action",
        source="workflow",
        idempotency_key=f"decision:{workflow.version}:{kind}:{stage.id}",
        stage_id=stage.id,
        dispatch=True,
        payload=payload,
    )


async def ensure_decision_gate_wake(db: AsyncSession, *, workflow: GroupWorkflow) -> GroupWorkflowEvent | None:
    """For stuck awaiting_approval stages: ensure DM exists and a pending decision_action."""
    if workflow.status != "awaiting_approval" or workflow.current_stage_id is None:
        return None
    stage = await db.scalar(
        select(GroupWorkflowStage).where(GroupWorkflowStage.id == workflow.current_stage_id)
    )
    if stage is None or stage.status != "awaiting_approval":
        return None
    group = await db.scalar(select(Group).where(Group.id == workflow.group_id))
    if group is not None and group.decision_maker_participant_id is None:
        from app.services.group_decision.seed import ensure_group_decision_maker_from_group

        await ensure_group_decision_maker_from_group(
            db,
            group=group,
            goal=group.description or group.name,
            require_ready=False,
        )
    existing = await db.scalar(
        select(GroupWorkflowEvent)
        .where(
            GroupWorkflowEvent.workflow_id == workflow.id,
            GroupWorkflowEvent.event_type == "decision_action",
            GroupWorkflowEvent.stage_id == stage.id,
            GroupWorkflowEvent.dispatch_state.in_(("pending", "claimed", "dispatched")),
        )
        .order_by(GroupWorkflowEvent.created_at.desc())
        .limit(1)
    )
    if existing is not None and existing.dispatch_state in {"pending", "claimed"}:
        return existing
    if existing is not None and existing.dispatch_state == "dispatched":
        # Re-queue so a previously skipped/no-DM wake can fire after backfill.
        existing.dispatch_state = "pending"
        existing.dispatched_at = None
        await db.flush()
        return existing
    return await _decision_action(db, workflow=workflow, stage=stage, kind="approval_required")


async def _locked_item(db: AsyncSession, item_id: uuid.UUID) -> tuple[GroupWorkflow, GroupWorkflowStage, GroupWorkflowItem]:
    item_result = await db.execute(select(GroupWorkflowItem).where(GroupWorkflowItem.id == item_id).with_for_update())
    item = item_result.scalar_one_or_none()
    if item is None:
        raise GroupWorkflowServiceError("workflow_item_not_found", "Workflow item was not found")
    workflow_result = await db.execute(select(GroupWorkflow).where(GroupWorkflow.id == item.workflow_id).with_for_update())
    workflow = workflow_result.scalar_one()
    if workflow.status == "paused":
        raise GroupWorkflowServiceError("workflow_paused", "Workflow is paused")
    if workflow.status == "completed":
        raise GroupWorkflowServiceError("workflow_completed", "Workflow is completed")
    stage_result = await db.execute(select(GroupWorkflowStage).where(GroupWorkflowStage.id == item.stage_id).with_for_update())
    return workflow, stage_result.scalar_one(), item


async def start_item(db: AsyncSession, *, item_id: uuid.UUID, actor_participant_id: uuid.UUID, expected_version: int | None = None) -> WorkflowTransition:
    workflow, stage, item = await _locked_item(db, item_id)
    if item.assignee_participant_id not in {None, actor_participant_id, workflow.leader_participant_id}:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assignee or group leader can start this item")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if item.status == "pending":
        item.status, item.version = "in_progress", item.version + 1
        workflow.version += 1
        await _event(db, workflow=workflow, event_type="item_started", source="agent", actor_participant_id=actor_participant_id,
                     stage_id=stage.id, item_id=item.id, idempotency_key=f"item:{item.id}:started:{item.version}")
    return WorkflowTransition(workflow, stage, None, None)


async def submit_evidence(db: AsyncSession, *, item_id: uuid.UUID, actor_participant_id: uuid.UUID, evidence: dict, expected_version: int | None = None) -> WorkflowTransition:
    workflow, stage, item = await _locked_item(db, item_id)
    if item.assignee_participant_id not in {actor_participant_id, workflow.leader_participant_id}:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assignee or group leader can submit evidence")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if not evidence:
        raise GroupWorkflowServiceError("workflow_evidence_invalid", "Evidence must not be empty")
    if item.status != "done":
        item.evidence = [*(item.evidence or []), evidence]
        item.status, item.blocked_reason, item.version = "done", None, item.version + 1
        workflow.version += 1
        await _event(db, workflow=workflow, event_type="evidence_submitted", source="agent", actor_participant_id=actor_participant_id,
                     stage_id=stage.id, item_id=item.id, payload=evidence, idempotency_key=f"item:{item.id}:evidence:{item.version}")
    return await _reconcile(db, workflow=workflow, stage=stage)


async def set_blocked(db: AsyncSession, *, item_id: uuid.UUID, actor_participant_id: uuid.UUID, reason: str) -> WorkflowTransition:
    workflow, stage, item = await _locked_item(db, item_id)
    if item.assignee_participant_id not in {actor_participant_id, workflow.leader_participant_id}:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assignee or group leader can block this item")
    if not reason.strip():
        raise GroupWorkflowServiceError("workflow_block_reason_invalid", "Block reason must not be empty")
    item.status, item.blocked_reason, item.version = "blocked", reason.strip(), item.version + 1
    workflow.version += 1
    await _event(db, workflow=workflow, event_type="item_blocked", source="agent", actor_participant_id=actor_participant_id,
                 stage_id=stage.id, item_id=item.id, payload={"reason": item.blocked_reason}, idempotency_key=f"item:{item.id}:blocked:{item.version}")
    action = await _leader_action(db, workflow=workflow, stage=stage, kind="blocker", item=item)
    return WorkflowTransition(workflow, stage, None, action)


async def clear_blocked(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    expected_version: int | None = None,
) -> WorkflowTransition:
    workflow, stage, item = await _locked_item(db, item_id)
    if item.assignee_participant_id not in {actor_participant_id, workflow.leader_participant_id}:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assignee or group leader can unblock this item")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if item.status == "blocked":
        item.status, item.blocked_reason, item.version = "in_progress", None, item.version + 1
        workflow.version += 1
        await _event(
            db, workflow=workflow, event_type="item_unblocked", source="agent",
            actor_participant_id=actor_participant_id, stage_id=stage.id, item_id=item.id,
            idempotency_key=f"item:{item.id}:unblocked:{item.version}",
        )
    return await _reconcile(db, workflow=workflow, stage=stage)


async def pause(db: AsyncSession, *, workflow_id: uuid.UUID, actor_participant_id: uuid.UUID) -> GroupWorkflow:
    result = await db.execute(select(GroupWorkflow).where(GroupWorkflow.id == workflow_id).with_for_update())
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise GroupWorkflowServiceError("workflow_not_found", "Workflow was not found")
    if workflow.status not in {"completed", "paused"}:
        workflow.status, workflow.version = "paused", workflow.version + 1
        await _event(db, workflow=workflow, event_type="workflow_paused", source="human",
                     actor_participant_id=actor_participant_id, idempotency_key=f"workflow:paused:{workflow.version}")
    return workflow


async def resume(db: AsyncSession, *, workflow_id: uuid.UUID, actor_participant_id: uuid.UUID) -> GroupWorkflow:
    result = await db.execute(select(GroupWorkflow).where(GroupWorkflow.id == workflow_id).with_for_update())
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise GroupWorkflowServiceError("workflow_not_found", "Workflow was not found")
    if workflow.status == "paused":
        stage_result = await db.execute(select(GroupWorkflowStage).where(GroupWorkflowStage.id == workflow.current_stage_id))
        stage = stage_result.scalar_one_or_none()
        workflow.status, workflow.version = ("awaiting_approval" if stage and stage.status == "awaiting_approval" else "active"), workflow.version + 1
        await _event(db, workflow=workflow, event_type="workflow_resumed", source="human",
                     actor_participant_id=actor_participant_id, idempotency_key=f"workflow:resumed:{workflow.version}")
        if stage is not None:
            await _leader_action(db, workflow=workflow, stage=stage, kind="workflow_resumed")
    return workflow


async def _reconcile(db: AsyncSession, *, workflow: GroupWorkflow, stage: GroupWorkflowStage) -> WorkflowTransition:
    result = await db.execute(select(GroupWorkflowItem).where(GroupWorkflowItem.stage_id == stage.id))
    items = list(result.scalars().all())
    if any(item.status == "blocked" for item in items):
        return WorkflowTransition(workflow, stage, None, await _leader_action(db, workflow=workflow, stage=stage, kind="blocker"))
    if not items or any(item.status != "done" for item in items):
        return WorkflowTransition(workflow, stage, None, None)
    needs_confirm = stage.requires_approval or await _workflow_okr_requires_human_confirm(
        db, tenant_id=workflow.tenant_id
    )
    if needs_confirm:
        stage.status, workflow.status = "awaiting_approval", "awaiting_approval"
        workflow.version += 1
        action = await _leader_action(db, workflow=workflow, stage=stage, kind="approval_required")
        await _decision_action(db, workflow=workflow, stage=stage, kind="approval_required")
        # Optional OKR nudge while waiting; "arrival" events fire only after confirm.
        await _notify_okr(workflow, "approval_required", stage)
        return WorkflowTransition(workflow, stage, None, action)
    return await _complete_stage(db, workflow=workflow, stage=stage, source="workflow")


async def confirm_stage(
    db: AsyncSession,
    *,
    stage_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    allow_decision_maker: bool = False,
) -> WorkflowTransition:
    result = await db.execute(select(GroupWorkflowStage).where(GroupWorkflowStage.id == stage_id).with_for_update())
    stage = result.scalar_one_or_none()
    if stage is None:
        raise GroupWorkflowServiceError("workflow_stage_not_found", "Workflow stage was not found")
    workflow_result = await db.execute(select(GroupWorkflow).where(GroupWorkflow.id == stage.workflow_id).with_for_update())
    workflow = workflow_result.scalar_one()
    if workflow.status == "paused":
        raise GroupWorkflowServiceError("workflow_paused", "Workflow is paused")
    if stage.status != "awaiting_approval":
        raise GroupWorkflowServiceError("workflow_stage_not_awaiting_approval", "Stage is not awaiting approval")

    group = await db.scalar(select(Group).where(Group.id == workflow.group_id))
    is_decision_maker = bool(
        allow_decision_maker
        and group is not None
        and group.decision_maker_participant_id == actor_participant_id
    )
    if group is not None and group.leader_participant_id == actor_participant_id and not is_decision_maker:
        raise GroupWorkflowServiceError(
            "workflow_confirm_denied",
            "Group leader cannot confirm stages; the decision maker or a human manager must confirm",
        )
    source = "decision_maker" if is_decision_maker else "human"
    return await _complete_stage(
        db, workflow=workflow, stage=stage, source=source, actor_participant_id=actor_participant_id
    )


async def _complete_stage(db: AsyncSession, *, workflow: GroupWorkflow, stage: GroupWorkflowStage, source: str, actor_participant_id: uuid.UUID | None = None) -> WorkflowTransition:
    stage.status, stage.completed_at = "completed", _now()
    workflow.version += 1
    await _event(db, workflow=workflow, event_type="stage_completed", source=source, actor_participant_id=actor_participant_id,
                 stage_id=stage.id, idempotency_key=f"stage:{stage.id}:completed")
    confirmed = source in {"human", "decision_maker"}
    await _notify_okr(workflow, "stage_completed", stage, confirmed=confirmed)
    next_result = await db.execute(select(GroupWorkflowStage).where(
        GroupWorkflowStage.workflow_id == workflow.id, GroupWorkflowStage.position == stage.position + 1,
    ).with_for_update())
    next_stage = next_result.scalar_one_or_none()
    if next_stage is None:
        workflow.status, workflow.current_stage_id = "completed", None
        action = await _leader_action(db, workflow=workflow, stage=stage, kind="workflow_completed")
        await _notify_okr(workflow, "workflow_completed", stage, confirmed=confirmed)
        return WorkflowTransition(workflow, stage, None, action)
    next_stage.status, next_stage.started_at = "active", _now()
    workflow.status, workflow.current_stage_id = "active", next_stage.id
    workflow.version += 1
    await _event(db, workflow=workflow, event_type="stage_activated", source="workflow", stage_id=next_stage.id,
                 idempotency_key=f"stage:{next_stage.id}:activated")
    action = await _leader_action(db, workflow=workflow, stage=next_stage, kind="stage_activated")
    await _notify_okr(workflow, "stage_activated", next_stage, confirmed=confirmed)
    return WorkflowTransition(workflow, stage, next_stage, action)
