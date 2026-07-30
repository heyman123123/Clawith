"""Transactional state transitions for evidence-driven group collaboration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group_workflow import GroupWorkflow, GroupWorkflowEvent, GroupWorkflowItem, GroupWorkflowStage
from app.services.group_workflow.contracts import WorkflowPlan
from app.services.group_workflow.templates import preset_workflow


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


async def _leader_action(
    db: AsyncSession, *, workflow: GroupWorkflow, stage: GroupWorkflowStage, kind: str,
    item: GroupWorkflowItem | None = None,
) -> GroupWorkflowEvent:
    return await _event(
        db, workflow=workflow, event_type="leader_action", source="workflow",
        idempotency_key=f"leader:{workflow.version}:{kind}:{stage.id}:{item.id if item else '-'}",
        stage_id=stage.id, item_id=item.id if item else None, dispatch=True,
        payload={"kind": kind, "stage_title": stage.title, "item_title": item.title if item else None},
    )


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
    if stage.requires_approval:
        stage.status, workflow.status = "awaiting_approval", "awaiting_approval"
        workflow.version += 1
        action = await _leader_action(db, workflow=workflow, stage=stage, kind="approval_required")
        return WorkflowTransition(workflow, stage, None, action)
    return await _complete_stage(db, workflow=workflow, stage=stage, source="workflow")


async def confirm_stage(db: AsyncSession, *, stage_id: uuid.UUID, actor_participant_id: uuid.UUID) -> WorkflowTransition:
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
    return await _complete_stage(db, workflow=workflow, stage=stage, source="human", actor_participant_id=actor_participant_id)


async def _complete_stage(db: AsyncSession, *, workflow: GroupWorkflow, stage: GroupWorkflowStage, source: str, actor_participant_id: uuid.UUID | None = None) -> WorkflowTransition:
    stage.status, stage.completed_at = "completed", _now()
    workflow.version += 1
    await _event(db, workflow=workflow, event_type="stage_completed", source=source, actor_participant_id=actor_participant_id,
                 stage_id=stage.id, idempotency_key=f"stage:{stage.id}:completed")
    next_result = await db.execute(select(GroupWorkflowStage).where(
        GroupWorkflowStage.workflow_id == workflow.id, GroupWorkflowStage.position == stage.position + 1,
    ).with_for_update())
    next_stage = next_result.scalar_one_or_none()
    if next_stage is None:
        workflow.status, workflow.current_stage_id = "completed", None
        action = await _leader_action(db, workflow=workflow, stage=stage, kind="workflow_completed")
        return WorkflowTransition(workflow, stage, None, action)
    next_stage.status, next_stage.started_at = "active", _now()
    workflow.status, workflow.current_stage_id = "active", next_stage.id
    workflow.version += 1
    await _event(db, workflow=workflow, event_type="stage_activated", source="workflow", stage_id=next_stage.id,
                 idempotency_key=f"stage:{next_stage.id}:activated")
    action = await _leader_action(db, workflow=workflow, stage=next_stage, kind="stage_activated")
    return WorkflowTransition(workflow, stage, next_stage, action)
