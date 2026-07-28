"""Workflow metrics + workflow template API (P6)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.metrics import (
    WorkflowMetricDaily,
    WorkflowTemplate,
    WorkflowTemplateMatchEvent,
)
from app.models.user import User
from app.services import workflow_metrics as svc

router = APIRouter(prefix="/workflow-metrics", tags=["workflow-metrics"])


def _tenant_id(user: User) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    return user.tenant_id


class DailyMetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_date: date
    workflows_started: int
    workflows_succeeded: int
    workflows_failed: int
    steps_dispatched: int
    steps_quality_passed: int
    steps_quality_failed: int
    steps_delivery_approved: int
    steps_delivery_rejected: int
    sandbox_runs_total: int
    sandbox_runs_blocked: int
    skill_learning_total: int
    skill_learning_approved: int
    skill_learning_rejected: int
    evolution_events: int
    evolution_rollbacks: int
    tokens_input_total: int
    tokens_output_total: int
    quality_score_avg: float


class WorkflowTemplateIn(BaseModel):
    slug: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=2, max_length=200)
    summary: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    keywords: list[str] = Field(default_factory=list, max_length=64)
    recommended_roles: list[str] = Field(default_factory=list, max_length=32)
    quality_threshold: int = Field(default=80, ge=0, le=100)
    ao_provider: str | None = Field(default=None, max_length=50)
    ao_model: str | None = Field(default=None, max_length=100)
    status: str = Field(default="draft", pattern="^(draft|published|deprecated)$")


class WorkflowTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    summary: str
    tags: list[str]
    keywords: list[str]
    recommended_roles: list[str]
    quality_threshold: int
    status: str
    usage_count: int
    ao_provider: str | None
    ao_model: str | None


def _template_payload(row: WorkflowTemplate) -> dict[str, Any]:
    return WorkflowTemplateOut(
        id=row.id,
        slug=row.slug,
        title=row.title,
        summary=row.summary or "",
        tags=list(row.tags or []),
        keywords=list(row.keywords or []),
        recommended_roles=list(row.recommended_roles or []),
        quality_threshold=row.quality_threshold,
        status=row.status,
        usage_count=row.usage_count,
        ao_provider=row.ao_provider,
        ao_model=row.ao_model,
    ).model_dump(mode="json")


def _daily_payload(row: WorkflowMetricDaily) -> dict[str, Any]:
    return DailyMetricOut(
        metric_date=row.metric_date,
        workflows_started=row.workflows_started,
        workflows_succeeded=row.workflows_succeeded,
        workflows_failed=row.workflows_failed,
        steps_dispatched=row.steps_dispatched,
        steps_quality_passed=row.steps_quality_passed,
        steps_quality_failed=row.steps_quality_failed,
        steps_delivery_approved=row.steps_delivery_approved,
        steps_delivery_rejected=row.steps_delivery_rejected,
        sandbox_runs_total=row.sandbox_runs_total,
        sandbox_runs_blocked=row.sandbox_runs_blocked,
        skill_learning_total=row.skill_learning_total,
        skill_learning_approved=row.skill_learning_approved,
        skill_learning_rejected=row.skill_learning_rejected,
        evolution_events=row.evolution_events,
        evolution_rollbacks=row.evolution_rollbacks,
        tokens_input_total=row.tokens_input_total,
        tokens_output_total=row.tokens_output_total,
        quality_score_avg=float(row.quality_score_avg or 0.0),
    ).model_dump(mode="json")


@router.get("/daily", response_model=list[DailyMetricOut])
async def list_daily_metrics(
    days: int = Query(default=14, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = await svc.list_metrics(db, _tenant_id(current_user), days=days)
    return [_daily_payload(r) for r in rows]


@router.get("/dashboard")
async def dashboard_endpoint(
    days: int = Query(default=14, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = await svc.list_metrics(db, _tenant_id(current_user), days=days)
    return svc.dashboard_payload(rows)


@router.post("/aggregate", response_model=DailyMetricOut)
async def aggregate_today(
    day: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    target = day or date.today()
    await svc.aggregate_daily_metrics(db, _tenant_id(current_user), day=target)
    await db.commit()
    stmt = select(WorkflowMetricDaily).where(
        WorkflowMetricDaily.tenant_id == _tenant_id(current_user),
        WorkflowMetricDaily.metric_date == target,
    )
    fetched = (await db.scalars(stmt)).first()
    if fetched is None:
        raise HTTPException(status_code=500, detail="aggregate failed")
    return _daily_payload(fetched)


@router.post("/backfill")
async def backfill_endpoint(
    days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    results = await svc.backfill_recent(db, _tenant_id(current_user), days=days)
    await db.commit()
    return {"days": [r.to_dict() for r in results]}


@router.post("/cron/trigger")
async def trigger_metrics_cron(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Tenant-aware: run the nightly backfill for the user's tenant only."""
    tenant_id = _tenant_id(current_user)
    from app.database import async_session

    async with async_session() as session:
        results = await svc.backfill_recent(session, tenant_id, days=7)
        await session.commit()
    return {"days": [r.to_dict() for r in results], "tenants_processed": 1}


@router.get("/templates", response_model=list[WorkflowTemplateOut])
async def list_templates(
    status: str | None = Query(default="published", pattern="^(draft|published|deprecated)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(WorkflowTemplate).where(WorkflowTemplate.tenant_id == _tenant_id(current_user))
    if status:
        stmt = stmt.where(WorkflowTemplate.status == status)
    stmt = stmt.order_by(WorkflowTemplate.usage_count.desc(), WorkflowTemplate.updated_at.desc())
    rows = (await db.scalars(stmt)).all()
    return [_template_payload(r) for r in rows]


@router.post("/templates", response_model=WorkflowTemplateOut, status_code=201)
async def create_template(
    body: WorkflowTemplateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.scalar(
        select(WorkflowTemplate).where(
            WorkflowTemplate.tenant_id == _tenant_id(current_user),
            WorkflowTemplate.slug == body.slug,
        )
    )
    if existing is not None:
        existing.title = body.title
        existing.summary = body.summary
        existing.tags = list(body.tags)
        existing.keywords = list(body.keywords)
        existing.recommended_roles = list(body.recommended_roles)
        existing.quality_threshold = body.quality_threshold
        existing.ao_provider = body.ao_provider
        existing.ao_model = body.ao_model
        existing.status = body.status
        await db.flush()
        return _template_payload(existing)

    template = WorkflowTemplate(
        tenant_id=_tenant_id(current_user),
        slug=body.slug,
        title=body.title,
        summary=body.summary,
        tags=list(body.tags),
        keywords=list(body.keywords),
        recommended_roles=list(body.recommended_roles),
        quality_threshold=body.quality_threshold,
        ao_provider=body.ao_provider,
        ao_model=body.ao_model,
        status=body.status,
    )
    db.add(template)
    await db.flush()
    await db.commit()
    return _template_payload(template)


@router.post("/templates/{template_id}/record-match", response_model=WorkflowTemplateOut)
async def record_match_event(
    template_id: uuid.UUID,
    requirements_excerpt: str = "",
    match_score: float = 0.0,
    rank: int = 0,
    outcome: str = Query(default="presented", pattern="^(presented|selected|dismissed)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    template = await db.get(WorkflowTemplate, template_id)
    if template is None or template.tenant_id != _tenant_id(current_user):
        raise HTTPException(status_code=404, detail="template not found")
    db.add(
        WorkflowTemplateMatchEvent(
            tenant_id=template.tenant_id,
            template_id=template.id,
            requirements_excerpt=requirements_excerpt[:600],
            match_score=max(0.0, min(1.0, match_score)),
            rank=max(0, rank),
            outcome=outcome,
            actor_user_id=current_user.id,
        )
    )
    template.usage_count = (template.usage_count or 0) + 1
    await db.flush()
    await db.commit()
    return _template_payload(template)


__all__ = ["router"]
