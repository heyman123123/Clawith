"""HTTP boundary for evidence-driven group collaboration workflows."""
# ruff: noqa: B008
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.groups import _current_participant, _tenant_id
from app.core.security import get_current_user
from app.database import get_db
from app.models.group import Group, GroupMember
from app.models.group_workflow import (
    GroupWorkflow,
    GroupWorkflowDraft,
    GroupWorkflowEvent,
    GroupWorkflowItem,
    GroupWorkflowStage,
)
from app.models.user import User
from app.services import group_chat_service
from app.services.group_realtime import publish_group_workflow_changed
from app.services.group_workflow import service
from app.services.group_workflow.contracts import GroupWorkflowPlanError
from app.services.group_workflow.planning import GroupWorkflowPlanningError, confirmed_plan, generate_draft
from app.services.group_workflow.templates import preset_workflow

router = APIRouter(prefix="/api/groups", tags=["group-workflows"])


class PresetIn(BaseModel):
    kind: str = Field(pattern=r"^(default|agile|product_research)$")


class DraftIn(BaseModel):
    request: str = Field(min_length=1, max_length=8000)


class EvidenceIn(BaseModel):
    evidence: dict = Field(min_length=1)
    expected_version: int | None = Field(default=None, ge=1)


class BlockIn(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)
    expected_version: int | None = Field(default=None, ge=1)


class ItemPatchIn(BaseModel):
    status: str = Field(pattern=r"^(in_progress|unblock)$")
    expected_version: int | None = Field(default=None, ge=1)


def _workflow_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "workflow_invalid")
    if code.endswith("not_found") or code == "workflow_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif code in {"workflow_item_access_denied", "group_access_denied", "group_manager_required"}:
        status_code = status.HTTP_403_FORBIDDEN
    elif code == "workflow_version_conflict":
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail={"code": code, "message": str(exc)})


async def _scope(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID, participant_id: uuid.UUID, manager: bool = False
) -> tuple[Group, GroupMember]:
    try:
        group, membership, _ = await group_chat_service.authorize_group_member(
            db, tenant_id=tenant_id, group_id=group_id, participant_id=participant_id, human_only=True
        )
    except group_chat_service.GroupChatServiceError as exc:
        raise _workflow_error(exc) from exc
    if manager and membership.role != "manager":
        raise HTTPException(status_code=403, detail={"code": "group_manager_required", "message": "Group manager permission is required"})
    return group, membership


async def _ensure_workflow(db: AsyncSession, *, group: Group) -> GroupWorkflow:
    workflow = await service.get_current(db, group_id=group.id)
    if workflow is None:
        workflow = await service.create_default_workflow(
            db, tenant_id=group.tenant_id, group_id=group.id,
            leader_participant_id=group.leader_participant_id or group.created_by_participant_id,
            goal=group.description or group.name,
        )
    return workflow


async def _snapshot(db: AsyncSession, workflow: GroupWorkflow) -> dict:
    stages = list((await db.execute(
        select(GroupWorkflowStage).where(GroupWorkflowStage.workflow_id == workflow.id).order_by(GroupWorkflowStage.position)
    )).scalars().all())
    items = list((await db.execute(
        select(GroupWorkflowItem).where(GroupWorkflowItem.workflow_id == workflow.id).order_by(GroupWorkflowItem.created_at)
    )).scalars().all())
    action = (await db.execute(
        select(GroupWorkflowEvent).where(
            GroupWorkflowEvent.workflow_id == workflow.id,
            GroupWorkflowEvent.event_type == "leader_action",
            GroupWorkflowEvent.dispatch_state.in_(("pending", "claimed")),
        ).order_by(GroupWorkflowEvent.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    return {
        "id": str(workflow.id), "group_id": str(workflow.group_id), "leader_participant_id": str(workflow.leader_participant_id) if workflow.leader_participant_id else None,
        "name": workflow.name, "source": workflow.source, "status": workflow.status, "current_stage_id": str(workflow.current_stage_id) if workflow.current_stage_id else None,
        "version": workflow.version, "created_at": workflow.created_at, "updated_at": workflow.updated_at,
        "stages": [
            {"id": str(stage.id), "key": stage.stage_key, "title": stage.title, "goal": stage.goal, "position": stage.position,
             "status": stage.status, "requires_approval": stage.requires_approval, "acceptance_criteria": stage.acceptance_criteria,
             "owner_participant_id": str(stage.owner_participant_id) if stage.owner_participant_id else None,
             "started_at": stage.started_at, "completed_at": stage.completed_at}
            for stage in stages
        ],
        "items": [
            {"id": str(item.id), "stage_id": str(item.stage_id), "item_key": item.item_key, "title": item.title, "description": item.description,
             "assignee_participant_id": str(item.assignee_participant_id) if item.assignee_participant_id else None,
             "status": item.status, "evidence": item.evidence, "blocked_reason": item.blocked_reason, "version": item.version,
             "updated_at": item.updated_at}
            for item in items
        ],
        "leader_next_action": ({"id": str(action.id), "kind": action.payload.get("kind"), "stage_id": str(action.stage_id) if action.stage_id else None,
                                "item_id": str(action.item_id) if action.item_id else None, "payload": action.payload} if action else None),
    }


async def _notify(group_id: uuid.UUID, workflow: GroupWorkflow) -> None:
    await publish_group_workflow_changed(group_id=group_id, workflow_id=workflow.id, version=workflow.version)


@router.get("/{group_id}/workflow")
async def get_workflow(group_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id)
    workflow = await _ensure_workflow(db, group=group)
    # Backfill decision maker + re-queue wake for stuck awaiting_approval gates.
    await service.ensure_decision_gate_wake(db, workflow=workflow)
    return await _snapshot(db, workflow)


@router.get("/{group_id}/workflow/events")
async def list_events(
    group_id: uuid.UUID, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id)
    workflow = await _ensure_workflow(db, group=group)
    total = await db.scalar(select(func.count()).select_from(GroupWorkflowEvent).where(GroupWorkflowEvent.workflow_id == workflow.id)) or 0
    rows = list((await db.execute(select(GroupWorkflowEvent).where(GroupWorkflowEvent.workflow_id == workflow.id).order_by(GroupWorkflowEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return {"items": [{"id": str(event.id), "event_type": event.event_type, "actor_participant_id": str(event.actor_participant_id) if event.actor_participant_id else None,
                       "stage_id": str(event.stage_id) if event.stage_id else None, "item_id": str(event.item_id) if event.item_id else None,
                       "source": event.source, "payload": event.payload, "created_at": event.created_at} for event in rows],
            "page": page, "page_size": page_size, "total": total}


@router.post("/{group_id}/workflow/preset")
async def replace_preset(group_id: uuid.UUID, body: PresetIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id, manager=True)
    try:
        workflow = await service.replace_workflow_from_plan(db, tenant_id=tenant_id, group_id=group.id, leader_participant_id=group.leader_participant_id or group.created_by_participant_id,
                                                            plan=preset_workflow(body.kind, goal=group.description or group.name, leader_participant_id=group.leader_participant_id or group.created_by_participant_id), actor_participant_id=participant.id)
    except service.GroupWorkflowServiceError as exc:
        raise _workflow_error(exc) from exc
    await db.commit()
    await _notify(group.id, workflow)
    return await _snapshot(db, workflow)


@router.post("/{group_id}/workflow/drafts")
async def create_draft(group_id: uuid.UUID, body: DraftIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id, manager=True)
    draft = await generate_draft(db, tenant_id=tenant_id, group_id=group.id, creator=current_user, actor_participant_id=participant.id, request=body.request)
    return _draft_out(draft)


def _draft_out(draft: GroupWorkflowDraft) -> dict:
    return {"id": str(draft.id), "group_id": str(draft.group_id), "request": draft.request, "plan": draft.plan, "status": draft.status,
            "error_code": draft.error_code, "error_message": draft.error_message, "confirmed_at": draft.confirmed_at,
            "created_at": draft.created_at, "updated_at": draft.updated_at}


@router.get("/{group_id}/workflow/drafts/{draft_id}")
async def get_draft(group_id: uuid.UUID, draft_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id, manager=True)
    draft = await db.scalar(select(GroupWorkflowDraft).where(GroupWorkflowDraft.id == draft_id, GroupWorkflowDraft.group_id == group_id))
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "workflow_draft_not_found", "message": "Workflow draft was not found"})
    return _draft_out(draft)


@router.post("/{group_id}/workflow/drafts/{draft_id}/confirm")
async def confirm_draft(group_id: uuid.UUID, draft_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id, manager=True)
    draft = await db.scalar(select(GroupWorkflowDraft).where(GroupWorkflowDraft.id == draft_id, GroupWorkflowDraft.group_id == group_id).with_for_update())
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "workflow_draft_not_found", "message": "Workflow draft was not found"})
    try:
        workflow = await service.replace_workflow_from_plan(db, tenant_id=tenant_id, group_id=group.id, leader_participant_id=group.leader_participant_id or group.created_by_participant_id,
                                                            plan=confirmed_plan(draft), actor_participant_id=participant.id)
    except (service.GroupWorkflowServiceError, GroupWorkflowPlanningError, GroupWorkflowPlanError) as exc:
        raise _workflow_error(exc) from exc
    draft.status, draft.confirmed_at = "confirmed", datetime.now(UTC)
    await db.commit()
    await _notify(group.id, workflow)
    return await _snapshot(db, workflow)


async def _item_transition(group_id: uuid.UUID, item_id: uuid.UUID, body: ItemPatchIn | EvidenceIn | BlockIn, *, action: str, current_user: User, db: AsyncSession):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id)
    scoped_item = await db.scalar(
        select(GroupWorkflowItem)
        .join(GroupWorkflow, GroupWorkflow.id == GroupWorkflowItem.workflow_id)
        .where(GroupWorkflowItem.id == item_id, GroupWorkflow.group_id == group.id)
    )
    if scoped_item is None:
        raise HTTPException(status_code=404, detail={"code": "workflow_item_not_found", "message": "Workflow item was not found"})
    try:
        if action == "start":
            transition = await service.start_item(db, item_id=item_id, actor_participant_id=participant.id, expected_version=body.expected_version)
        elif action == "unblock":
            transition = await service.clear_blocked(db, item_id=item_id, actor_participant_id=participant.id, expected_version=body.expected_version)
        elif action == "evidence":
            transition = await service.submit_evidence(db, item_id=item_id, actor_participant_id=participant.id, evidence=body.evidence, expected_version=body.expected_version)
        else:
            transition = await service.set_blocked(db, item_id=item_id, actor_participant_id=participant.id, reason=body.reason)
    except service.GroupWorkflowServiceError as exc:
        raise _workflow_error(exc) from exc
    if transition.workflow.group_id != group.id:
        raise HTTPException(status_code=404, detail={"code": "workflow_item_not_found", "message": "Workflow item was not found"})
    await db.commit()
    await _notify(group.id, transition.workflow)
    return await _snapshot(db, transition.workflow)


@router.patch("/{group_id}/workflow/items/{item_id}")
async def patch_item(group_id: uuid.UUID, item_id: uuid.UUID, body: ItemPatchIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _item_transition(group_id, item_id, body, action="start" if body.status == "in_progress" else "unblock", current_user=current_user, db=db)


@router.post("/{group_id}/workflow/items/{item_id}/evidence")
async def submit_evidence(group_id: uuid.UUID, item_id: uuid.UUID, body: EvidenceIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _item_transition(group_id, item_id, body, action="evidence", current_user=current_user, db=db)


@router.post("/{group_id}/workflow/items/{item_id}/block")
async def block_item(group_id: uuid.UUID, item_id: uuid.UUID, body: BlockIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _item_transition(group_id, item_id, body, action="block", current_user=current_user, db=db)


@router.post("/{group_id}/workflow/stages/{stage_id}/confirm")
async def confirm_stage(group_id: uuid.UUID, stage_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id, manager=True)
    try:
        transition = await service.confirm_stage(db, stage_id=stage_id, actor_participant_id=participant.id)
    except service.GroupWorkflowServiceError as exc:
        raise _workflow_error(exc) from exc
    if transition.workflow.group_id != group.id:
        raise HTTPException(status_code=404, detail={"code": "workflow_stage_not_found", "message": "Workflow stage was not found"})
    await db.commit()
    await _notify(group.id, transition.workflow)
    return await _snapshot(db, transition.workflow)


async def _workflow_status(group_id: uuid.UUID, *, paused: bool, current_user: User, db: AsyncSession):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    group, _ = await _scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id, manager=True)
    workflow = await _ensure_workflow(db, group=group)
    try:
        workflow = await (service.pause(db, workflow_id=workflow.id, actor_participant_id=participant.id) if paused else service.resume(db, workflow_id=workflow.id, actor_participant_id=participant.id))
    except service.GroupWorkflowServiceError as exc:
        raise _workflow_error(exc) from exc
    await db.commit()
    await _notify(group.id, workflow)
    return await _snapshot(db, workflow)


@router.post("/{group_id}/workflow/pause")
async def pause_workflow(group_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _workflow_status(group_id, paused=True, current_user=current_user, db=db)


@router.post("/{group_id}/workflow/resume")
async def resume_workflow(group_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _workflow_status(group_id, paused=False, current_user=current_user, db=db)
