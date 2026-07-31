"""Human manager approve/reject APIs for group decision requests."""
# ruff: noqa: B008
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.groups import _current_participant, _tenant_id
from app.core.security import get_current_user
from app.database import get_db
from app.models.group import Group, GroupMember
from app.models.user import User
from app.services.group_decision import service as decision_service

router = APIRouter(prefix="/api/groups", tags=["group-decisions"])


class RejectDecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ApproveDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _decision_error(exc: decision_service.GroupDecisionError) -> HTTPException:
    status = 404 if exc.code.endswith("not_found") else 400
    if exc.code.endswith("denied"):
        status = 403
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


async def _manager_scope(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID, participant_id: uuid.UUID
) -> Group:
    group = await db.scalar(
        select(Group).where(Group.id == group_id, Group.tenant_id == tenant_id, Group.deleted_at.is_(None))
    )
    if group is None:
        raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": "Group not found"})
    membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.participant_id == participant_id,
            GroupMember.removed_at.is_(None),
            GroupMember.role == "manager",
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail={"code": "group_manager_required", "message": "Manager only"})
    return group


@router.get("/{group_id}/decisions")
async def list_group_decisions(
    group_id: uuid.UUID,
    status: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    await _manager_scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id)
    decisions = await decision_service.list_decisions(db, group_id=group_id, status=status)
    return [decision_service.decision_to_dict(item) for item in decisions]


@router.post("/{group_id}/decisions/{decision_id}/approve")
async def approve_group_decision(
    group_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: ApproveDecisionIn | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    await _manager_scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id)
    try:
        decision = await decision_service.approve_decision(
            db, decision_id=decision_id, actor_participant_id=participant.id, note=(body.note if body else None)
        )
    except decision_service.GroupDecisionError as exc:
        raise _decision_error(exc) from exc
    if decision.group_id != group_id:
        raise HTTPException(status_code=404, detail={"code": "decision_not_found", "message": "Not found"})
    await db.commit()
    return decision_service.decision_to_dict(decision)


@router.post("/{group_id}/decisions/{decision_id}/reject")
async def reject_group_decision(
    group_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: RejectDecisionIn | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    await _manager_scope(db, tenant_id=tenant_id, group_id=group_id, participant_id=participant.id)
    try:
        decision = await decision_service.reject_decision(
            db,
            decision_id=decision_id,
            actor_participant_id=participant.id,
            reason=(body.reason if body else None),
        )
    except decision_service.GroupDecisionError as exc:
        raise _decision_error(exc) from exc
    if decision.group_id != group_id:
        raise HTTPException(status_code=404, detail={"code": "decision_not_found", "message": "Not found"})
    await db.commit()
    return decision_service.decision_to_dict(decision)
