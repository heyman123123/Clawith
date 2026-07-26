"""HR review board API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.hr_review_session_service import (
    HrReviewError,
    generate_team_building_proposals,
    hr_session_to_dict,
    open_governance_topup_session,
    open_team_building_session,
    select_proposal,
)
from app.services.project_team_builder import HRPlanningError

router = APIRouter(prefix="/api/hr-review", tags=["hr-review"])


class OpenHrSessionIn(BaseModel):
    session_type: Literal["team_building", "governance_topup"]
    name: str | None = Field(default=None, min_length=1, max_length=200)
    requirements: str | None = Field(default=None, min_length=1, max_length=20_000)
    context_payload: dict = Field(default_factory=dict)


class SelectProposalIn(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=64)


class HrReviewSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: uuid.UUID
    session_id: uuid.UUID
    session_type: str
    status: str
    proposals: list
    selected_proposal_id: str | None
    context_payload: dict
    created_at: datetime
    closed_at: datetime | None = None


class TeamPlanSelectionOut(BaseModel):
    roles: list
    wake_up_message: str
    project_name: str = ""
    requirements: str = ""


def _tenant_id(user: User) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    return user.tenant_id


@router.post("/sessions", response_model=HrReviewSessionOut, status_code=status.HTTP_201_CREATED)
async def create_hr_session(
    body: OpenHrSessionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = _tenant_id(current_user)
    try:
        if body.session_type == "team_building":
            if not body.name or not body.requirements:
                raise HTTPException(status_code=422, detail="name and requirements are required")
            hr_session = await open_team_building_session(
                db,
                tenant_id=tenant_id,
                user=current_user,
                name=body.name,
                requirements=body.requirements,
            )
        else:
            hr_session = await open_governance_topup_session(
                db,
                tenant_id=tenant_id,
                user=current_user,
                context_payload=body.context_payload,
            )
    except HrReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return hr_session_to_dict(hr_session)


@router.get("/sessions/{session_id}", response_model=HrReviewSessionOut)
async def get_hr_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.models.hr_review import HrReviewSession

    hr_session = await db.get(HrReviewSession, session_id)
    if hr_session is None:
        raise HTTPException(status_code=404, detail="HR review session not found")
    return hr_session_to_dict(hr_session)


@router.post("/sessions/{session_id}/generate", response_model=HrReviewSessionOut)
async def generate_hr_proposals(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = _tenant_id(current_user)
    try:
        hr_session = await generate_team_building_proposals(
            db,
            hr_session_id=session_id,
            tenant_id=tenant_id,
            creator_id=current_user.id,
        )
    except (HrReviewError, HRPlanningError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return hr_session_to_dict(hr_session)


@router.post("/sessions/{session_id}/select")
async def select_hr_proposal(
    session_id: uuid.UUID,
    body: SelectProposalIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await select_proposal(
            db,
            hr_session_id=session_id,
            proposal_id=body.proposal_id,
            user=current_user,
        )
    except HrReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
