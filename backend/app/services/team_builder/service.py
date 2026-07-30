"""Transaction-scoped application service for intelligent team drafts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team_builder import TeamBuildDraft, TeamProvisionJob, TeamProvisionMember
from app.models.user import User
from app.services.team_builder.errors import TeamBuilderError
from app.services.team_builder.planning import generate_team_plan, validate_team_plan


def _now() -> datetime:
    return datetime.now(UTC)


def _require_requirement(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 12_000:
        raise TeamBuilderError("team_requirement_invalid", "Requirement must contain between 1 and 12000 characters")
    return normalized


async def get_draft(db: AsyncSession, *, current_user: User, draft_id: uuid.UUID, lock: bool = False) -> TeamBuildDraft:
    if current_user.tenant_id is None:
        raise TeamBuilderError("tenant_required", "A tenant is required")
    statement = select(TeamBuildDraft).where(
        TeamBuildDraft.id == draft_id,
        TeamBuildDraft.tenant_id == current_user.tenant_id,
        TeamBuildDraft.creator_user_id == current_user.id,
    )
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    draft = result.scalar_one_or_none()
    if draft is None:
        raise TeamBuilderError("team_draft_not_found", "Team draft was not found")
    return draft


async def create_draft(
    db: AsyncSession,
    *,
    current_user: User,
    requirement: str,
    constraints: dict,
    group_name: str | None,
) -> TeamBuildDraft:
    if current_user.tenant_id is None:
        raise TeamBuilderError("tenant_required", "A tenant is required")
    draft = TeamBuildDraft(
        tenant_id=current_user.tenant_id,
        creator_user_id=current_user.id,
        requirement=_require_requirement(requirement),
        constraints=constraints,
        status="generating",
    )
    db.add(draft)
    await db.flush()
    try:
        plan = await generate_team_plan(
            db,
            tenant_id=current_user.tenant_id,
            user=current_user,
            requirement=draft.requirement,
            constraints=constraints,
            group_name=group_name,
        )
    except TeamBuilderError as exc:
        draft.status = "invalid"
        draft.error_code = exc.code
        draft.error_message = str(exc)
        await db.flush()
        return draft
    payload = plan.model_dump(mode="json")
    draft.generated_plan = payload
    draft.reviewed_plan = payload
    draft.status = "ready"
    await db.flush()
    return draft


async def update_draft(
    db: AsyncSession,
    *,
    current_user: User,
    draft_id: uuid.UUID,
    reviewed_plan: object,
) -> TeamBuildDraft:
    draft = await get_draft(db, current_user=current_user, draft_id=draft_id, lock=True)
    if draft.status not in {"ready", "invalid"}:
        raise TeamBuilderError("team_draft_not_editable", "Team draft cannot be edited in its current state")
    plan = validate_team_plan(reviewed_plan)
    draft.reviewed_plan = plan.model_dump(mode="json")
    draft.plan_version += 1
    draft.confirmed_plan_version = None
    draft.confirmed_at = None
    draft.status = "ready"
    draft.error_code = None
    draft.error_message = None
    await db.flush()
    return draft


async def confirm_draft(
    db: AsyncSession,
    *,
    current_user: User,
    draft_id: uuid.UUID,
    plan_version: int,
    idempotency_key: str,
) -> TeamProvisionJob:
    draft = await get_draft(db, current_user=current_user, draft_id=draft_id, lock=True)
    key = idempotency_key.strip()
    if not key or len(key) > 120:
        raise TeamBuilderError("team_idempotency_key_invalid", "A valid idempotency key is required")
    existing = await db.execute(
        select(TeamProvisionJob).where(TeamProvisionJob.draft_id == draft.id, TeamProvisionJob.idempotency_key == key)
    )
    if job := existing.scalar_one_or_none():
        return job
    if draft.status != "ready" or draft.plan_version != plan_version:
        raise TeamBuilderError("team_draft_stale", "Review the latest team plan before confirming")
    if not isinstance(draft.reviewed_plan, dict):
        raise TeamBuilderError("team_plan_missing", "Team draft has no reviewed plan")
    plan = validate_team_plan(draft.reviewed_plan)
    if current_user.tenant_id is None:
        raise TeamBuilderError("tenant_required", "A tenant is required")
    job = TeamProvisionJob(
        tenant_id=current_user.tenant_id,
        draft_id=draft.id,
        requesting_user_id=current_user.id,
        idempotency_key=key,
        status="queued",
    )
    db.add(job)
    await db.flush()
    for member in plan.members:
        db.add(
            TeamProvisionMember(
                tenant_id=current_user.tenant_id,
                job_id=job.id,
                member_key=member.member_key,
                source=member.source,
                role_spec=member.model_dump(mode="json"),
                status="pending",
                agent_id=member.existing_agent_id,
            )
        )
    draft.status = "confirmed"
    draft.confirmed_plan_version = draft.plan_version
    draft.confirmed_at = _now()
    await db.flush()
    return job


async def get_job(db: AsyncSession, *, current_user: User, job_id: uuid.UUID) -> TeamProvisionJob:
    if current_user.tenant_id is None:
        raise TeamBuilderError("tenant_required", "A tenant is required")
    result = await db.execute(
        select(TeamProvisionJob).where(
            TeamProvisionJob.id == job_id,
            TeamProvisionJob.tenant_id == current_user.tenant_id,
            TeamProvisionJob.requesting_user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise TeamBuilderError("team_job_not_found", "Team provisioning job was not found")
    return job
