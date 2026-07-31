"""Transactional state transitions for evidence-driven group collaboration."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group, GroupMember
from app.models.group_workflow import (
    GroupWorkflow,
    GroupWorkflowChangeRequest,
    GroupWorkflowEvent,
    GroupWorkflowItem,
    GroupWorkflowStage,
    GroupWorkflowTaskDependency,
)
from app.models.participant import Participant
from app.services import group_file_service
from app.services.group_workflow.contracts import WorkflowPlan
from app.services.group_workflow.templates import preset_workflow

logger = logging.getLogger(__name__)

_SOURCE_CODE_EVIDENCE_MARKER = "[evidence_policy:source_code]"
_SOURCE_CODE_EXTENSIONS = frozenset({
    ".c", ".cc", ".cs", ".css", ".go", ".html", ".java", ".js", ".jsx",
    ".kt", ".php", ".py", ".rb", ".rs", ".sh", ".sql", ".ts", ".tsx",
})


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
    """Deprecated gate helper — OKR must not block stage advancement.

    Kept for call-site compatibility / tests; always returns False. Stage gates
    come only from ``stage.requires_approval`` (decision maker / manager).
    """
    _ = db, tenant_id
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
    ready_items: tuple[GroupWorkflowItem, ...] = ()


def _now() -> datetime:
    return datetime.now(UTC)


def _evidence_workspace_paths(evidence: dict) -> list[str]:
    values: list[object] = [evidence.get("workspace_path")]
    multiple = evidence.get("workspace_paths")
    if isinstance(multiple, list):
        values.extend(multiple)
    paths: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        path = value.strip().replace("\\", "/").removeprefix("workspace/")
        if path:
            paths.append(path)
    return list(dict.fromkeys(paths))


async def _validate_source_code_evidence(
    db: AsyncSession,
    *,
    workflow: GroupWorkflow,
    item: GroupWorkflowItem,
    actor_participant_id: uuid.UUID,
    evidence: dict,
) -> None:
    if _SOURCE_CODE_EVIDENCE_MARKER not in (getattr(item, "description", "") or ""):
        return
    paths = _evidence_workspace_paths(evidence)
    code_paths = [
        path for path in paths
        if path.startswith("代码/") and PurePosixPath(path).suffix.lower() in _SOURCE_CODE_EXTENSIONS
    ]
    if not code_paths:
        raise GroupWorkflowServiceError(
            "workflow_source_code_evidence_required",
            "This development item requires a source file under 代码/ in workspace_path or workspace_paths",
        )
    test_result = evidence.get("test_result")
    if not isinstance(test_result, str) or not test_result.strip():
        raise GroupWorkflowServiceError(
            "workflow_test_result_required",
            "This development item requires a non-empty test_result",
        )
    for path in code_paths:
        try:
            value = await group_file_service.read_workspace_file(
                db,
                tenant_id=workflow.tenant_id,
                group_id=workflow.group_id,
                actor_participant_id=actor_participant_id,
                path=path,
            )
        except group_file_service.GroupFileServiceError as exc:
            raise GroupWorkflowServiceError(
                "workflow_source_code_missing",
                f"Required source evidence is unavailable: {path}",
            ) from exc
        if len(value.content.strip()) < 20:
            raise GroupWorkflowServiceError(
                "workflow_source_code_invalid",
                f"Required source evidence is empty or too short: {path}",
            )


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
    try:
        async with db.begin_nested():
            db.add(value)
            await db.flush()
        return value
    except IntegrityError:
        raced = await db.scalar(
            select(GroupWorkflowEvent).where(
                GroupWorkflowEvent.workflow_id == workflow.id,
                GroupWorkflowEvent.idempotency_key == idempotency_key,
            )
        )
        if raced is not None:
            return raced
        raise


async def _queue_ready_item(
    db: AsyncSession,
    *,
    workflow: GroupWorkflow,
    stage: GroupWorkflowStage,
    item: GroupWorkflowItem,
) -> GroupWorkflowEvent:
    """Create the idempotent dispatch record consumed by the task worker."""
    return await _event(
        db,
        workflow=workflow,
        event_type="task_ready",
        source="workflow",
        stage_id=stage.id,
        item_id=item.id,
        idempotency_key=f"item:{item.id}:ready:{item.version}",
        payload={
            "task_key": item.item_key,
            "task_title": item.title,
            "assignee_participant_id": str(item.assignee_participant_id) if item.assignee_participant_id else None,
            "acceptance_criteria": item.acceptance_criteria or [],
        },
        dispatch=True,
    )


async def _refresh_ready_items(
    db: AsyncSession,
    *,
    workflow: GroupWorkflow,
    stage_ids: set[uuid.UUID] | None = None,
) -> tuple[GroupWorkflowItem, ...]:
    """Mark active-stage pending tasks ready only when every dependency is accepted."""
    stages = list(
        (
            await db.execute(
                select(GroupWorkflowStage).where(GroupWorkflowStage.workflow_id == workflow.id)
            )
        ).scalars().all()
    )
    active_stage_ids = {
        stage.id for stage in stages if stage.status == "active" and (stage_ids is None or stage.id in stage_ids)
    }
    if not active_stage_ids:
        return ()
    items = list(
        (
            await db.execute(
                select(GroupWorkflowItem).where(
                    GroupWorkflowItem.workflow_id == workflow.id,
                    GroupWorkflowItem.stage_id.in_(active_stage_ids),
                    GroupWorkflowItem.status == "pending",
                )
            )
        ).scalars().all()
    )
    if not items:
        return ()
    dependencies = list(
        (
            await db.execute(
                select(GroupWorkflowTaskDependency).where(GroupWorkflowTaskDependency.workflow_id == workflow.id)
            )
        ).scalars().all()
    )
    all_items = list(
        (
            await db.execute(
                select(GroupWorkflowItem).where(GroupWorkflowItem.workflow_id == workflow.id)
            )
        ).scalars().all()
    )
    by_id = {item.id: item for item in all_items}
    predecessors: dict[uuid.UUID, list[uuid.UUID]] = {}
    for dependency in dependencies:
        predecessors.setdefault(dependency.successor_item_id, []).append(dependency.predecessor_item_id)
    stage_by_id = {stage.id: stage for stage in stages}
    ready_items: list[GroupWorkflowItem] = []
    for item in items:
        predecessor_ids = predecessors.get(item.id, [])
        if all(by_id.get(predecessor_id) is not None and by_id[predecessor_id].status == "done" for predecessor_id in predecessor_ids):
            item.status = "ready"
            item.blocked_reason = None
            item.version += 1
            ready_items.append(item)
            await _queue_ready_item(db, workflow=workflow, stage=stage_by_id[item.stage_id], item=item)
    if ready_items:
        workflow.version += 1
    return tuple(ready_items)


async def _dependency_block_reason(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    item_id: uuid.UUID,
) -> str | None:
    rows = list(
        (
            await db.execute(
                select(GroupWorkflowTaskDependency, GroupWorkflowItem)
                .join(GroupWorkflowItem, GroupWorkflowItem.id == GroupWorkflowTaskDependency.predecessor_item_id)
                .where(
                    GroupWorkflowTaskDependency.workflow_id == workflow_id,
                    GroupWorkflowTaskDependency.successor_item_id == item_id,
                )
            )
        ).all()
    )
    failed = [item.title for _dependency, item in rows if item.status in {"blocked", "failed"}]
    if failed:
        return f"等待前置任务恢复：{'、'.join(failed[:3])}"
    return None


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
    items_by_key: dict[str, GroupWorkflowItem] = {}
    for stage, stage_plan in zip(stages, plan.stages, strict=True):
        for item_plan in stage_plan.items:
            item = GroupWorkflowItem(
                workflow_id=workflow.id, stage_id=stage.id, item_key=item_plan.item_key,
                title=item_plan.title, description=item_plan.description,
                acceptance_criteria=item_plan.acceptance_criteria,
                assignee_participant_id=item_plan.assignee_participant_id or stage.owner_participant_id,
            )
            db.add(item)
            items_by_key[f"{stage_plan.key}.{item_plan.item_key}"] = item
    await db.flush()
    for stage_plan in plan.stages:
        for item_plan in stage_plan.items:
            successor = items_by_key[f"{stage_plan.key}.{item_plan.item_key}"]
            for dependency_key in item_plan.depends_on:
                db.add(
                    GroupWorkflowTaskDependency(
                        workflow_id=workflow.id,
                        predecessor_item_id=items_by_key[dependency_key].id,
                        successor_item_id=successor.id,
                    )
                )
    await db.flush()
    workflow.current_stage_id = stages[0].id
    await _event(db, workflow=workflow, event_type="workflow_created", source="system",
                 idempotency_key="workflow:created", stage_id=stages[0].id,
                 payload={"source": plan.source})
    await _refresh_ready_items(db, workflow=workflow, stage_ids={stages[0].id})
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
    db: AsyncSession,
    *,
    workflow: GroupWorkflow,
    stage: GroupWorkflowStage,
    kind: str,
    item: GroupWorkflowItem | None = None,
    extra_payload: dict | None = None,
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
    if extra_payload:
        payload.update(extra_payload)
    return await _event(
        db, workflow=workflow, event_type="leader_action", source="workflow",
        idempotency_key=f"leader:{workflow.version}:{kind}:{stage.id}:{item.id if item else '-'}",
        stage_id=stage.id, item_id=item.id if item else None, dispatch=True,
        payload=payload,
    )


async def notify_leader_decision_resolved(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    workflow_id: uuid.UUID | None,
    stage_id: uuid.UUID | None,
    title: str,
    status: str,
    summary: str,
    category: str | None = None,
) -> GroupWorkflowEvent | None:
    """Wake the group leader after a decision reaches a terminal status."""
    workflow = None
    if workflow_id is not None:
        workflow = await db.scalar(select(GroupWorkflow).where(GroupWorkflow.id == workflow_id))
    if workflow is None:
        workflow = await db.scalar(select(GroupWorkflow).where(GroupWorkflow.group_id == group_id))
    if workflow is None:
        return None
    stage = None
    if stage_id is not None:
        stage = await db.scalar(select(GroupWorkflowStage).where(GroupWorkflowStage.id == stage_id))
    if stage is None and workflow.current_stage_id is not None:
        stage = await db.scalar(
            select(GroupWorkflowStage).where(GroupWorkflowStage.id == workflow.current_stage_id)
        )
    if stage is None:
        return None
    return await _leader_action(
        db,
        workflow=workflow,
        stage=stage,
        kind="decision_resolved",
        extra_payload={
            "decision_title": title,
            "decision_status": status,
            "decision_summary": (summary or "")[:300],
            "decision_category": category,
        },
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
    """For stuck awaiting_approval stages: ensure DM exists and a pending decision_action.

    Also clears legacy OKR-forced gates on stages that do not require approval:
    those should auto-advance so the group is not stuck waiting for humans.
    """
    if workflow.status != "awaiting_approval" or workflow.current_stage_id is None:
        return None
    stage = await db.scalar(
        select(GroupWorkflowStage).where(GroupWorkflowStage.id == workflow.current_stage_id)
    )
    if stage is None or stage.status != "awaiting_approval":
        return None
    if not stage.requires_approval:
        # Evidence already forced reconcile into awaiting_approval under the old
        # OKR gate; complete now without human/DM wait.
        result = await db.execute(select(GroupWorkflowItem).where(GroupWorkflowItem.stage_id == stage.id))
        items = list(result.scalars().all())
        if items and all(item.status == "done" for item in items):
            transition = await _complete_stage(db, workflow=workflow, stage=stage, source="workflow")
            return transition.leader_action
        # Items incomplete: reopen active so the leader can finish evidence.
        stage.status = "active"
        workflow.status = "active"
        workflow.version += 1
        await db.flush()
        return await _leader_action(db, workflow=workflow, stage=stage, kind="stage_activated")
    group = await db.scalar(select(Group).where(Group.id == workflow.group_id))
    if group is not None:
        from app.services.group_decision.seed import ensure_group_decision_maker_from_group

        # Creates DM when missing; also backfills cross-space grants on existing DMs.
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
    if item.assignee_participant_id != actor_participant_id:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assigned member can start this item")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if item.status != "ready":
        raise GroupWorkflowServiceError("workflow_item_not_ready", "Task is waiting for dependency completion")
    if item.status == "ready":
        item.status, item.started_at, item.version = "in_progress", _now(), item.version + 1
        workflow.version += 1
        await _event(db, workflow=workflow, event_type="item_started", source="agent", actor_participant_id=actor_participant_id,
                     stage_id=stage.id, item_id=item.id, idempotency_key=f"item:{item.id}:started:{item.version}")
    return WorkflowTransition(workflow, stage, None, None)


async def submit_evidence(db: AsyncSession, *, item_id: uuid.UUID, actor_participant_id: uuid.UUID, evidence: dict, expected_version: int | None = None) -> WorkflowTransition:
    workflow, stage, item = await _locked_item(db, item_id)
    if item.assignee_participant_id is not None and item.assignee_participant_id != actor_participant_id:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assigned member can submit completion evidence")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if not evidence:
        raise GroupWorkflowServiceError("workflow_evidence_invalid", "Evidence must not be empty")
    if item.status not in {"ready", "in_progress", "awaiting_approval"}:
        raise GroupWorkflowServiceError("workflow_item_not_ready", "Task is not ready for evidence submission")
    await _validate_source_code_evidence(
        db,
        workflow=workflow,
        item=item,
        actor_participant_id=actor_participant_id,
        evidence=evidence,
    )
    newly_done = False
    if item.status != "done":
        item.evidence = [*(item.evidence or []), evidence]
        item.status, item.blocked_reason, item.completed_at, item.version = "done", None, _now(), item.version + 1
        if getattr(item, "started_at", None) is None:
            item.started_at = _now()
        item.failed_at, item.failure_code, item.failure_summary = None, None, None
        workflow.version += 1
        newly_done = True
        await _event(db, workflow=workflow, event_type="evidence_submitted", source="agent", actor_participant_id=actor_participant_id,
                     stage_id=stage.id, item_id=item.id, payload=evidence, idempotency_key=f"item:{item.id}:evidence:{item.version}")
    ready_items = await _refresh_ready_items(db, workflow=workflow, stage_ids={stage.id}) if newly_done else ()
    transition = await _reconcile(db, workflow=workflow, stage=stage)
    return WorkflowTransition(
        transition.workflow,
        transition.stage,
        transition.next_stage,
        transition.leader_action,
        (*ready_items, *getattr(transition, "ready_items", ())),
    )


async def set_blocked(db: AsyncSession, *, item_id: uuid.UUID, actor_participant_id: uuid.UUID, reason: str) -> WorkflowTransition:
    workflow, stage, item = await _locked_item(db, item_id)
    if item.assignee_participant_id != actor_participant_id:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assigned member can block this item")
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
    if item.assignee_participant_id != actor_participant_id:
        raise GroupWorkflowServiceError("workflow_item_access_denied", "Only the assigned member can unblock this item")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if item.status == "blocked":
        item.status, item.blocked_reason, item.version = "ready", None, item.version + 1
        workflow.version += 1
        await _event(
            db, workflow=workflow, event_type="item_unblocked", source="agent",
            actor_participant_id=actor_participant_id, stage_id=stage.id, item_id=item.id,
            idempotency_key=f"item:{item.id}:unblocked:{item.version}",
        )
    return await _reconcile(db, workflow=workflow, stage=stage)


async def retry_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    expected_version: int | None = None,
) -> WorkflowTransition:
    """A leader explicitly restarts failed or blocked work; no automatic reruns."""
    workflow, stage, item = await _locked_item(db, item_id)
    if workflow.leader_participant_id != actor_participant_id:
        raise GroupWorkflowServiceError("workflow_retry_denied", "Only the group leader can retry failed work")
    if expected_version is not None and item.version != expected_version:
        raise GroupWorkflowServiceError("workflow_version_conflict", "Workflow item has changed")
    if item.status not in {"blocked", "failed"}:
        raise GroupWorkflowServiceError("workflow_retry_invalid", "Only blocked or failed tasks can be retried")
    item.status, item.blocked_reason, item.failure_code, item.failure_summary = "pending", None, None, None
    item.failed_at = None
    item.version += 1
    workflow.version += 1
    await _event(
        db,
        workflow=workflow,
        event_type="item_retry_requested",
        source="agent",
        actor_participant_id=actor_participant_id,
        stage_id=stage.id,
        item_id=item.id,
        idempotency_key=f"item:{item.id}:retry:{item.version}",
    )
    ready_items = await _refresh_ready_items(db, workflow=workflow, stage_ids={stage.id})
    return WorkflowTransition(workflow, stage, None, None, ready_items)


async def _change_impact(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    target_item_id: uuid.UUID | None,
) -> dict:
    if target_item_id is None:
        return {"target_item_id": None, "affected_item_ids": [], "ready_item_ids": []}
    dependencies = list(
        (
            await db.execute(
                select(GroupWorkflowTaskDependency).where(GroupWorkflowTaskDependency.workflow_id == workflow_id)
            )
        ).scalars().all()
    )
    successors: dict[uuid.UUID, list[uuid.UUID]] = {}
    for dependency in dependencies:
        successors.setdefault(dependency.predecessor_item_id, []).append(dependency.successor_item_id)
    affected: set[uuid.UUID] = set()
    queue = list(successors.get(target_item_id, []))
    while queue:
        item_id = queue.pop()
        if item_id in affected:
            continue
        affected.add(item_id)
        queue.extend(successors.get(item_id, []))
    items = list(
        (
            await db.execute(
                select(GroupWorkflowItem).where(GroupWorkflowItem.id.in_(affected))
            )
        ).scalars().all()
    ) if affected else []
    return {
        "target_item_id": str(target_item_id),
        "affected_item_ids": [str(item_id) for item_id in sorted(affected, key=str)],
        "ready_item_ids": [str(item.id) for item in items if item.status == "ready"],
    }


async def request_task_change(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    requester_participant_id: uuid.UUID,
    kind: str,
    target_item_id: uuid.UUID | None,
    after: dict,
    reason: str,
) -> GroupWorkflowChangeRequest:
    """Record a proposed DAG change; only the group leader may confirm it later."""
    if kind not in {"add", "split", "reconnect", "acceptance"}:
        raise GroupWorkflowServiceError("workflow_change_kind_invalid", "Unsupported workflow task change")
    if not reason.strip() or not isinstance(after, dict):
        raise GroupWorkflowServiceError("workflow_change_invalid", "Change reason and payload are required")
    workflow = await db.scalar(select(GroupWorkflow).where(GroupWorkflow.id == workflow_id).with_for_update())
    if workflow is None:
        raise GroupWorkflowServiceError("workflow_not_found", "Workflow was not found")
    target = None
    if target_item_id is not None:
        target = await db.scalar(select(GroupWorkflowItem).where(GroupWorkflowItem.id == target_item_id).with_for_update())
        if target is None or target.workflow_id != workflow.id:
            raise GroupWorkflowServiceError("workflow_item_not_found", "Workflow item was not found")
        if target.status != "pending":
            raise GroupWorkflowServiceError("workflow_change_started_task", "Only unstarted tasks can be changed")
    elif kind != "add":
        raise GroupWorkflowServiceError("workflow_change_target_required", "This change requires an unstarted task")
    change = GroupWorkflowChangeRequest(
        workflow_id=workflow.id,
        target_item_id=target.id if target else None,
        requester_participant_id=requester_participant_id,
        kind=kind,
        before={
            "item_key": target.item_key,
            "title": target.title,
            "description": target.description,
            "acceptance_criteria": target.acceptance_criteria,
        } if target else {},
        after=after,
        impact=await _change_impact(db, workflow_id=workflow.id, target_item_id=target_item_id),
        reason=reason.strip(),
    )
    db.add(change)
    await db.flush()
    stage = await db.scalar(
        select(GroupWorkflowStage).where(GroupWorkflowStage.id == (target.stage_id if target else workflow.current_stage_id))
    )
    if stage is not None:
        await _event(
            db,
            workflow=workflow,
            event_type="task_change_requested",
            source="agent",
            actor_participant_id=requester_participant_id,
            stage_id=stage.id,
            item_id=target.id if target else None,
            idempotency_key=f"task-change:{change.id}:requested",
            payload={"change_request_id": str(change.id), "kind": kind, "impact": change.impact},
        )
        await _leader_action(
            db,
            workflow=workflow,
            stage=stage,
            kind="task_change_confirmation",
            item=target,
            extra_payload={"change_request_id": str(change.id), "change_kind": kind, "impact": change.impact},
        )
    return change


async def _replace_dependencies(
    db: AsyncSession,
    *,
    workflow: GroupWorkflow,
    item: GroupWorkflowItem,
    predecessor_ids: list[uuid.UUID],
) -> None:
    if item.id in predecessor_ids or len(predecessor_ids) != len(set(predecessor_ids)):
        raise GroupWorkflowServiceError("workflow_dependency_invalid", "Task dependencies must be distinct and cannot include the task itself")
    predecessors = list(
        (
            await db.execute(
                select(GroupWorkflowItem).where(
                    GroupWorkflowItem.workflow_id == workflow.id,
                    GroupWorkflowItem.id.in_(predecessor_ids),
                )
            )
        ).scalars().all()
    )
    if len(predecessors) != len(predecessor_ids):
        raise GroupWorkflowServiceError("workflow_dependency_invalid", "A referenced dependency is outside this workflow")
    stages = {
        stage.id: stage
        for stage in (
            await db.execute(select(GroupWorkflowStage).where(GroupWorkflowStage.workflow_id == workflow.id))
        ).scalars().all()
    }
    if any(stages[predecessor.stage_id].position > stages[item.stage_id].position for predecessor in predecessors):
        raise GroupWorkflowServiceError("workflow_dependency_later_stage", "A task cannot depend on a later stage")
    await db.execute(
        delete(GroupWorkflowTaskDependency).where(
            GroupWorkflowTaskDependency.workflow_id == workflow.id,
            GroupWorkflowTaskDependency.successor_item_id == item.id,
        )
    )
    for predecessor_id in predecessor_ids:
        db.add(
            GroupWorkflowTaskDependency(
                workflow_id=workflow.id,
                predecessor_item_id=predecessor_id,
                successor_item_id=item.id,
            )
        )
    await db.flush()
    dependencies = list(
        (
            await db.execute(
                select(GroupWorkflowTaskDependency).where(GroupWorkflowTaskDependency.workflow_id == workflow.id)
            )
        ).scalars().all()
    )
    successors: dict[uuid.UUID, list[uuid.UUID]] = {}
    for dependency in dependencies:
        successors.setdefault(dependency.predecessor_item_id, []).append(dependency.successor_item_id)
    visiting: set[uuid.UUID] = set()
    visited: set[uuid.UUID] = set()

    def visit(item_id: uuid.UUID) -> None:
        if item_id in visiting:
            raise GroupWorkflowServiceError("workflow_dependency_cycle", "Task dependency change would create a cycle")
        if item_id in visited:
            return
        visiting.add(item_id)
        for successor_item_id in successors.get(item_id, []):
            visit(successor_item_id)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in successors:
        visit(item_id)


async def confirm_task_change(
    db: AsyncSession,
    *,
    change_request_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    approved: bool,
) -> GroupWorkflowChangeRequest:
    """Apply a pending request only after the actual group leader confirms it."""
    change = await db.scalar(
        select(GroupWorkflowChangeRequest).where(GroupWorkflowChangeRequest.id == change_request_id).with_for_update()
    )
    if change is None:
        raise GroupWorkflowServiceError("workflow_change_not_found", "Workflow change request was not found")
    workflow = await db.scalar(select(GroupWorkflow).where(GroupWorkflow.id == change.workflow_id).with_for_update())
    if workflow is None:
        raise GroupWorkflowServiceError("workflow_not_found", "Workflow was not found")
    if workflow.leader_participant_id != actor_participant_id:
        raise GroupWorkflowServiceError("workflow_change_confirm_denied", "Only the group leader can confirm this change")
    if change.status != "pending":
        raise GroupWorkflowServiceError("workflow_change_not_pending", "Workflow change is already resolved")
    change.confirmer_participant_id = actor_participant_id
    target = None
    if change.target_item_id is not None:
        target = await db.scalar(select(GroupWorkflowItem).where(GroupWorkflowItem.id == change.target_item_id).with_for_update())
        if target is None or target.status != "pending":
            raise GroupWorkflowServiceError("workflow_change_started_task", "Only unstarted tasks can be changed")
    if not approved:
        change.status, change.rejected_at = "rejected", _now()
        return change

    if change.kind == "acceptance":
        criteria = change.after.get("acceptance_criteria")
        if target is None or not isinstance(criteria, list) or not all(isinstance(value, str) and value.strip() for value in criteria):
            raise GroupWorkflowServiceError("workflow_change_invalid", "Acceptance changes require non-empty criteria")
        target.acceptance_criteria = [value.strip() for value in criteria]
        target.version += 1
    elif change.kind == "reconnect":
        dependency_values = change.after.get("depends_on_item_ids")
        if target is None or not isinstance(dependency_values, list):
            raise GroupWorkflowServiceError("workflow_change_invalid", "Dependency changes require predecessor item IDs")
        try:
            predecessor_ids = [uuid.UUID(str(value)) for value in dependency_values]
        except (TypeError, ValueError) as exc:
            raise GroupWorkflowServiceError("workflow_change_invalid", "Dependency IDs must be UUIDs") from exc
        await _replace_dependencies(db, workflow=workflow, item=target, predecessor_ids=predecessor_ids)
    else:
        stage_value = change.after.get("stage_id")
        try:
            stage_id = uuid.UUID(str(stage_value))
        except (TypeError, ValueError) as exc:
            raise GroupWorkflowServiceError("workflow_change_invalid", "Added tasks require a stage ID") from exc
        stage = await db.scalar(select(GroupWorkflowStage).where(GroupWorkflowStage.id == stage_id).with_for_update())
        if stage is None or stage.workflow_id != workflow.id or stage.status == "completed":
            raise GroupWorkflowServiceError("workflow_change_invalid", "Tasks can only be added to an uncompleted workflow stage")
        title = str(change.after.get("title") or "").strip()
        description = str(change.after.get("description") or "").strip()
        item_key = str(change.after.get("item_key") or "").strip()
        criteria = change.after.get("acceptance_criteria")
        if not title or not description or not item_key or not isinstance(criteria, list) or not criteria:
            raise GroupWorkflowServiceError("workflow_change_invalid", "Added tasks require key, title, description and acceptance criteria")
        duplicate = await db.scalar(
            select(GroupWorkflowItem.id).where(GroupWorkflowItem.stage_id == stage.id, GroupWorkflowItem.item_key == item_key)
        )
        if duplicate is not None:
            raise GroupWorkflowServiceError("workflow_change_invalid", "Task key already exists in this stage")
        assignee_value = change.after.get("assignee_participant_id")
        try:
            assignee_id = uuid.UUID(str(assignee_value)) if assignee_value else None
        except ValueError as exc:
            raise GroupWorkflowServiceError("workflow_change_invalid", "Assignee must be a UUID") from exc
        target = GroupWorkflowItem(
            workflow_id=workflow.id,
            stage_id=stage.id,
            item_key=item_key,
            title=title,
            description=description,
            acceptance_criteria=[str(value).strip() for value in criteria],
            assignee_participant_id=assignee_id,
        )
        db.add(target)
        await db.flush()
        dependency_values = change.after.get("depends_on_item_ids", [])
        if not isinstance(dependency_values, list):
            raise GroupWorkflowServiceError("workflow_change_invalid", "Task dependencies must be a list")
        try:
            predecessor_ids = [uuid.UUID(str(value)) for value in dependency_values]
        except (TypeError, ValueError) as exc:
            raise GroupWorkflowServiceError("workflow_change_invalid", "Dependency IDs must be UUIDs") from exc
        await _replace_dependencies(db, workflow=workflow, item=target, predecessor_ids=predecessor_ids)

    change.status, change.confirmed_at = "confirmed", _now()
    workflow.version += 1
    await _event(
        db,
        workflow=workflow,
        event_type="task_change_confirmed",
        source="agent",
        actor_participant_id=actor_participant_id,
        item_id=target.id if target else None,
        idempotency_key=f"task-change:{change.id}:confirmed",
        payload={"change_request_id": str(change.id), "kind": change.kind},
    )
    await _refresh_ready_items(db, workflow=workflow)
    return change


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
    # Only explicit approval stages gate; OKR project push must not block advancement.
    if stage.requires_approval:
        stage.status, workflow.status = "awaiting_approval", "awaiting_approval"
        workflow.version += 1
        action = await _leader_action(db, workflow=workflow, stage=stage, kind="approval_required")
        await _decision_action(db, workflow=workflow, stage=stage, kind="approval_required")
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
    confirmed = source in {"human", "decision_maker", "workflow"}
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
    ready_items = await _refresh_ready_items(db, workflow=workflow, stage_ids={next_stage.id})
    action = await _leader_action(db, workflow=workflow, stage=next_stage, kind="stage_activated")
    await _notify_okr(workflow, "stage_activated", next_stage, confirmed=confirmed)
    return WorkflowTransition(workflow, stage, next_stage, action, ready_items)
