"""Skill marketplace API (P5).

Exposes:

* ``GET /api/skill-market`` — list published listings.
* ``POST /api/skill-market`` — create / idempotent re-create.
* ``POST /api/skill-market/{id}/publish`` / ``/disable`` — admin actions.
* ``POST /api/skill-market/{id}/sandbox`` — run a smoke test in subprocess backend.
* ``POST /api/skill-market/{id}/request-approval`` — open a human approval.
* ``POST /api/skill-market/approvals/{id}/resolve`` — approve / reject.
* ``GET /api/skill-market/learn-records`` — skill learning audit log.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.skill_market import (
    AgentSkillBinding,
    SkillApprovalRequest,
    SkillLearningRecord,
    SkillMarketListing,
    SkillSandboxRun,
)
from app.models.user import User
from app.services import skill_market_service as svc

router = APIRouter(prefix="/skill-market", tags=["skill-market"])


def _tenant_id(user: User) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    return user.tenant_id


class CreateListingIn(BaseModel):
    skill_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    keywords: list[str] = Field(default_factory=list, max_length=64)
    risk_level: str = Field(default="low", pattern="^(low|medium|high)$")
    share_scope: str = Field(default="team", pattern="^(private|team|company)$")
    files: list[dict[str, str]] = Field(default_factory=list, max_length=32)


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    listing_id: uuid.UUID
    skill_id: uuid.UUID
    title: str
    summary: str
    keywords: list[str]
    risk_level: str
    status: str
    share_scope: str
    install_count: int
    published_at: str | None = None


class SandboxRunIn(BaseModel):
    language: str = Field(default="python", pattern="^(python|bash|node)$")
    code: str = Field(min_length=0, max_length=20_000)
    timeout: int = Field(default=30, ge=1, le=600)
    allow_network: bool = False


class SandboxRunOut(BaseModel):
    run_id: uuid.UUID
    status: str
    exit_code: int | None
    duration_ms: int | None
    stdout: str
    stderr: str
    error: str | None
    risk_level: str
    requires_human_review: bool
    rationale: str


class ApprovalIn(BaseModel):
    sandbox_run_id: uuid.UUID | None = None
    rationale: str | None = Field(default=None, max_length=4000)
    kind: str = Field(default="high_risk_publish", pattern="^(high_risk_publish|high_risk_install|policy_exception)$")


class ApprovalResolveIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    decision_notes: str | None = Field(default=None, max_length=4000)


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    approval_id: uuid.UUID
    listing_id: uuid.UUID
    decision: str
    decision_notes: str | None
    rationale: str | None
    created_at: str
    resolved_at: str | None


class LearningStartIn(BaseModel):
    agent_id: uuid.UUID
    skill_id: uuid.UUID
    listing_id: uuid.UUID | None = None
    trigger_reason: str = Field(min_length=4, max_length=4000)
    detected_gap: str | None = Field(default=None, max_length=4000)
    risk_level: str = Field(default="low", pattern="^(low|medium|high)$")


class LearningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_id: uuid.UUID
    agent_id: uuid.UUID
    skill_id: uuid.UUID | None
    listing_id: uuid.UUID | None
    status: str
    detected_risk_level: str
    trigger_reason: str | None
    created_at: str | None
    completed_at: str | None
    failure_reason: str | None


def _approval_payload(row: SkillApprovalRequest) -> dict[str, Any]:
    return {
        "approval_id": row.id,
        "listing_id": row.listing_id,
        "decision": row.decision,
        "decision_notes": row.decision_notes,
        "rationale": row.rationale,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _listing_payload(row: SkillMarketListing) -> dict[str, Any]:
    payload = ListingOut(
        listing_id=row.id,
        skill_id=row.skill_id,
        title=row.title,
        summary=row.summary or "",
        keywords=list(row.keywords or []),
        risk_level=row.risk_level,
        status=row.status,
        share_scope=row.share_scope,
        install_count=row.install_count,
        published_at=row.published_at.isoformat() if row.published_at else None,
    )
    return payload.model_dump(mode="json")


@router.get("", response_model=list[ListingOut])
async def list_market_endpoint(
    status: str | None = Query(default="published", pattern="^(draft|in_review|published|disabled|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = await svc.list_market(db, _tenant_id(current_user), status=status, limit=limit)
    return [_listing_payload(r) for r in rows]


@router.post("", response_model=ListingOut, status_code=201)
async def create_listing_endpoint(
    body: CreateListingIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    files_snapshot = [(f.get("path", ""), f.get("content", "")) for f in body.files if f.get("path")]
    listing = await svc.create_listing(
        db,
        tenant_id=_tenant_id(current_user),
        skill_id=body.skill_id,
        title=body.title,
        summary=body.summary,
        keywords=body.keywords,
        risk_level=body.risk_level,
        share_scope=body.share_scope,
        publisher_user_id=current_user.id,
        files_snapshot=files_snapshot or None,
    )
    await db.commit()
    return _listing_payload(listing)


@router.post("/{listing_id}/publish", response_model=ListingOut)
async def publish_listing_endpoint(
    listing_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    listing = await svc.publish_listing(db, listing_id)
    if listing.tenant_id != _tenant_id(current_user):
        raise HTTPException(status_code=404, detail="listing not found")
    await db.commit()
    return _listing_payload(listing)


@router.post("/{listing_id}/disable", response_model=ListingOut)
async def disable_listing_endpoint(
    listing_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    listing = await svc.disable_listing(db, listing_id)
    if listing.tenant_id != _tenant_id(current_user):
        raise HTTPException(status_code=404, detail="listing not found")
    await db.commit()
    return _listing_payload(listing)


@router.post("/{listing_id}/sandbox", response_model=SandboxRunOut)
async def run_sandbox_endpoint(
    listing_id: uuid.UUID,
    body: SandboxRunIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    listing = await db.get(SkillMarketListing, listing_id)
    if listing is None or listing.tenant_id != _tenant_id(current_user):
        raise HTTPException(status_code=404, detail="listing not found")

    result = await svc.run_skill_smoke_test(
        db,
        tenant_id=listing.tenant_id,
        skill_id=listing.skill_id,
        listing_id=listing.id,
        language=body.language,
        code=body.code,
        timeout=body.timeout,
        triggered_by_user_id=current_user.id,
        allow_network=body.allow_network,
    )
    await db.commit()
    return SandboxRunOut(
        run_id=uuid.UUID(result.run_id),
        status=result.status,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        stdout=result.stdout,
        stderr=result.stderr,
        error=result.error,
        risk_level=result.detected_risk_level,
        requires_human_review=result.risk_assessment.requires_human_review,
        rationale=result.risk_assessment.rationale,
    ).model_dump(mode="json")


@router.post("/{listing_id}/request-approval", response_model=ApprovalOut, status_code=201)
async def request_approval_endpoint(
    listing_id: uuid.UUID,
    body: ApprovalIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    listing = await db.get(SkillMarketListing, listing_id)
    if listing is None or listing.tenant_id != _tenant_id(current_user):
        raise HTTPException(status_code=404, detail="listing not found")
    approval = await svc.open_approval(
        db,
        tenant_id=listing.tenant_id,
        listing_id=listing.id,
        sandbox_run_id=body.sandbox_run_id,
        requester_user_id=current_user.id,
        rationale=body.rationale,
        kind=body.kind,
    )
    await db.commit()
    return _approval_payload(approval)


@router.post("/approvals/{approval_id}/resolve", response_model=ApprovalOut)
async def resolve_approval_endpoint(
    approval_id: uuid.UUID,
    body: ApprovalResolveIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    approval = await svc.resolve_approval(
        db,
        approval_id,
        decision=body.decision,
        reviewer_user_id=current_user.id,
        decision_notes=body.decision_notes,
    )
    if approval.tenant_id != _tenant_id(current_user):
        raise HTTPException(status_code=404, detail="approval not found")
    await db.commit()
    return _approval_payload(approval)


@router.post("/learning/start", response_model=LearningOut, status_code=201)
async def start_learning_endpoint(
    body: LearningStartIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    record = await svc.start_learning(
        db,
        tenant_id=_tenant_id(current_user),
        agent_id=body.agent_id,
        trigger_reason=body.trigger_reason,
        detected_gap=body.detected_gap,
        skill_id=body.skill_id,
        listing_id=body.listing_id,
        risk_level=body.risk_level,
    )
    await db.commit()
    return svc.serialize_learning(record)


@router.get("/learning/records", response_model=list[LearningOut])
async def list_learning_records_endpoint(
    agent_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import select

    stmt = select(SkillLearningRecord).where(SkillLearningRecord.tenant_id == _tenant_id(current_user))
    if agent_id:
        stmt = stmt.where(SkillLearningRecord.agent_id == agent_id)
    stmt = stmt.order_by(SkillLearningRecord.created_at.desc()).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [svc.serialize_learning(r) for r in rows]


@router.get("/sandbox-runs", response_model=list[SandboxRunOut])
async def list_sandbox_runs(
    listing_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import select

    stmt = select(SkillSandboxRun).where(SkillSandboxRun.tenant_id == _tenant_id(current_user))
    if listing_id:
        stmt = stmt.where(SkillSandboxRun.listing_id == listing_id)
    stmt = stmt.order_by(SkillSandboxRun.created_at.desc()).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [
        SandboxRunOut(
            run_id=r.id,
            status=r.status,
            exit_code=r.exit_code,
            duration_ms=r.duration_ms,
            stdout=(r.stdout or "")[:2000],
            stderr=(r.stderr or "")[:1000],
            error=r.error,
            risk_level=r.detected_risk_level,
            requires_human_review=False,
            rationale="recorded",
        ).model_dump(mode="json")
        for r in rows
    ]


@router.get("/agents/{agent_id}/skills")
async def list_agent_skills(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import select  # noqa: F821

    skills = await svc.agent_enabled_skills(db, agent_id)
    tenant_id = _tenant_id(current_user)
    bindings = (
        await db.scalars(
            select(AgentSkillBinding).where(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.tenant_id == tenant_id,
            )
        )
    ).all()
    binding_by_skill = {b.skill_id: b for b in bindings}
    return [
        {
            "skill_id": str(s.id),
            "name": s.name,
            "category": s.category,
            "is_enabled": binding_by_skill[s.id].is_enabled if s.id in binding_by_skill else False,
            "installed_at": binding_by_skill[s.id].installed_at.isoformat() if s.id in binding_by_skill else None,
        }
        for s in skills
    ]


__all__ = ["router"]
