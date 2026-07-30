"""HTTP API for AI-assisted team design and durable provisioning jobs."""

# ruff: noqa: B008

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.team_builder import TeamBuildDraft, TeamProvisionJob, TeamProvisionMember
from app.models.user import User
from app.services.team_builder import service
from app.services.team_builder.errors import TeamBuilderError

router = APIRouter(prefix="/api/team-build-drafts", tags=["team-builder"])


class CreateTeamBuildDraftIn(BaseModel):
    requirement: str = Field(min_length=1, max_length=12_000)
    constraints: dict[str, Any] = Field(default_factory=dict)
    group_name: str | None = Field(default=None, max_length=200)
    workflow_preset: str | None = Field(default="default", max_length=40)


class PatchTeamBuildDraftIn(BaseModel):
    reviewed_plan: dict[str, Any]


class ReviseTeamBuildDraftIn(BaseModel):
    feedback: str = Field(min_length=1, max_length=8000)
    scope: str = Field(default="both", pattern="^(members|workflow|both)$")


class ApplyWorkflowPresetIn(BaseModel):
    preset: str = Field(pattern="^(default|agile|product_research)$")


class ConfirmTeamBuildDraftIn(BaseModel):
    plan_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TeamBuildDraftOut(BaseModel):
    id: uuid.UUID
    status: str
    requirement: str
    constraints: dict[str, Any]
    generated_plan: dict[str, Any] | None
    reviewed_plan: dict[str, Any] | None
    plan_version: int
    confirmed_plan_version: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class TeamProvisionMemberOut(BaseModel):
    member_key: str
    source: str
    status: str
    agent_id: uuid.UUID | None
    participant_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    model_config = {"from_attributes": True}


class TeamProvisionJobOut(BaseModel):
    id: uuid.UUID
    draft_id: uuid.UUID
    status: str
    group_id: uuid.UUID | None
    leader_participant_id: uuid.UUID | None
    session_id: uuid.UUID | None
    activation_message_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    members: list[TeamProvisionMemberOut]


class TeamProvisionJobSummaryOut(BaseModel):
    id: uuid.UUID
    draft_id: uuid.UUID
    status: str
    group_id: uuid.UUID | None
    session_id: uuid.UUID | None
    error_message: str | None
    model_config = {"from_attributes": True}


class TeamBuildHistoryItemOut(BaseModel):
    draft: TeamBuildDraftOut
    job: TeamProvisionJobSummaryOut | None


def _error(exc: TeamBuilderError) -> HTTPException:
    if exc.code in {"team_draft_not_found", "team_job_not_found"}:
        code = status.HTTP_404_NOT_FOUND
    elif exc.code in {"team_draft_stale", "team_draft_not_editable"}:
        code = status.HTTP_409_CONFLICT
    elif exc.code == "tenant_required":
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc), "retryable": exc.retryable})


async def _job_out(db: AsyncSession, job: TeamProvisionJob) -> TeamProvisionJobOut:
    result = await db.execute(
        select(TeamProvisionMember).where(TeamProvisionMember.job_id == job.id).order_by(TeamProvisionMember.created_at)
    )
    return TeamProvisionJobOut(
        id=job.id,
        draft_id=job.draft_id,
        status=job.status,
        group_id=job.group_id,
        leader_participant_id=job.leader_participant_id,
        session_id=job.session_id,
        activation_message_id=job.activation_message_id,
        error_code=job.error_code,
        error_message=job.error_message,
        members=[TeamProvisionMemberOut.model_validate(member) for member in result.scalars().all()],
    )


async def _draft_out(db: AsyncSession, draft: TeamBuildDraft) -> TeamBuildDraftOut:
    """Load server-managed timestamps before Pydantic serializes an async ORM row."""
    await db.refresh(draft)
    return TeamBuildDraftOut.model_validate(draft)


@router.get("", response_model=list[TeamBuildHistoryItemOut])
async def list_team_build_history(
    limit: int = Query(default=30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's recent team builds and their latest provisioning result."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    result = await db.execute(
        select(TeamBuildDraft)
        .where(
            TeamBuildDraft.tenant_id == current_user.tenant_id,
            TeamBuildDraft.creator_user_id == current_user.id,
        )
        .order_by(TeamBuildDraft.updated_at.desc())
        .limit(limit)
    )
    drafts = list(result.scalars().all())
    if not drafts:
        return []
    job_result = await db.execute(
        select(TeamProvisionJob)
        .where(TeamProvisionJob.draft_id.in_([draft.id for draft in drafts]))
        .order_by(TeamProvisionJob.created_at.desc())
    )
    latest_job_by_draft: dict[uuid.UUID, TeamProvisionJob] = {}
    for job in job_result.scalars().all():
        latest_job_by_draft.setdefault(job.draft_id, job)
    return [
        TeamBuildHistoryItemOut(
            draft=await _draft_out(db, draft),
            job=TeamProvisionJobSummaryOut.model_validate(latest_job_by_draft[draft.id])
            if draft.id in latest_job_by_draft
            else None,
        )
        for draft in drafts
    ]


@router.post("", response_model=TeamBuildDraftOut, status_code=status.HTTP_201_CREATED)
async def create_team_build_draft(
    body: CreateTeamBuildDraftIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        draft = await service.create_draft(
            db,
            current_user=current_user,
            requirement=body.requirement,
            constraints=body.constraints,
            group_name=body.group_name,
            workflow_preset=body.workflow_preset or "default",
        )
        return await _draft_out(db, draft)
    except TeamBuilderError as exc:
        raise _error(exc) from exc


@router.get("/{draft_id}", response_model=TeamBuildDraftOut)
async def get_team_build_draft(
    draft_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        draft = await service.get_draft(db, current_user=current_user, draft_id=draft_id)
        return await _draft_out(db, draft)
    except TeamBuilderError as exc:
        raise _error(exc) from exc


@router.patch("/{draft_id}", response_model=TeamBuildDraftOut)
async def patch_team_build_draft(
    draft_id: uuid.UUID,
    body: PatchTeamBuildDraftIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        draft = await service.update_draft(
            db, current_user=current_user, draft_id=draft_id, reviewed_plan=body.reviewed_plan
        )
        return await _draft_out(db, draft)
    except TeamBuilderError as exc:
        raise _error(exc) from exc


@router.post("/{draft_id}/revise", response_model=TeamBuildDraftOut)
async def revise_team_build_draft(
    draft_id: uuid.UUID,
    body: ReviseTeamBuildDraftIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        draft = await service.revise_draft(
            db,
            current_user=current_user,
            draft_id=draft_id,
            feedback=body.feedback,
            scope=body.scope,
        )
        return await _draft_out(db, draft)
    except TeamBuilderError as exc:
        raise _error(exc) from exc


@router.post("/{draft_id}/workflow-preset", response_model=TeamBuildDraftOut)
async def apply_team_build_workflow_preset(
    draft_id: uuid.UUID,
    body: ApplyWorkflowPresetIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        draft = await service.apply_workflow_preset(
            db,
            current_user=current_user,
            draft_id=draft_id,
            preset=body.preset,
        )
        return await _draft_out(db, draft)
    except TeamBuilderError as exc:
        raise _error(exc) from exc


@router.post("/{draft_id}/confirm", response_model=TeamProvisionJobOut, status_code=status.HTTP_202_ACCEPTED)
async def confirm_team_build_draft(
    draft_id: uuid.UUID,
    body: ConfirmTeamBuildDraftIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        job = await service.confirm_draft(
            db,
            current_user=current_user,
            draft_id=draft_id,
            plan_version=body.plan_version,
            idempotency_key=body.idempotency_key,
        )
        return await _job_out(db, job)
    except TeamBuilderError as exc:
        raise _error(exc) from exc


@router.get("/jobs/{job_id}", response_model=TeamProvisionJobOut)
async def get_team_provision_job(
    job_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        job = await service.get_job(db, current_user=current_user, job_id=job_id)
        return await _job_out(db, job)
    except TeamBuilderError as exc:
        raise _error(exc) from exc
