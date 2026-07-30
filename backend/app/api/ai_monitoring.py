"""Administrator-only read API for privacy-safe AI interaction telemetry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.models.agent import Agent
from app.models.ai_interaction import AIInteractionLog
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User

router = APIRouter(prefix="/api/ai-monitoring", tags=["ai-monitoring"])


class AIInteractionSummaryOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str | None
    llm_model_id: uuid.UUID | None
    model_label: str | None
    provider: str
    model_name: str
    source: str
    invocation_kind: str
    status: str
    token_source: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    estimated_tokens: int
    duration_ms: int | None
    started_at: datetime
    finished_at: datetime
    created_at: datetime


class AIInteractionDetailOut(AIInteractionSummaryOut):
    session_id: str | None
    run_id: str | None
    request_context: dict[str, Any]
    response_content: str | None
    error: dict[str, Any] | None


class AIInteractionOverviewOut(BaseModel):
    calls_24h: int
    errors_24h: int
    total_tokens_24h: int
    page: int
    page_size: int
    total: int
    interactions: list[AIInteractionSummaryOut]


class AIInteractionPageOut(BaseModel):
    page: int
    page_size: int
    total: int
    interactions: list[AIInteractionSummaryOut]


async def _names(db: AsyncSession, rows: list[AIInteractionLog]) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    agent_ids = {row.agent_id for row in rows if row.agent_id is not None}
    model_ids = {row.llm_model_id for row in rows if row.llm_model_id is not None}
    agents: dict[uuid.UUID, str] = {}
    models: dict[uuid.UUID, str] = {}
    if agent_ids:
        result = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
        agents = dict(result.all())
    if model_ids:
        result = await db.execute(select(LLMModel.id, LLMModel.label).where(LLMModel.id.in_(model_ids)))
        models = dict(result.all())
    return agents, models


def _summary(
    row: AIInteractionLog,
    agents: dict[uuid.UUID, str],
    models: dict[uuid.UUID, str],
) -> AIInteractionSummaryOut:
    return AIInteractionSummaryOut(
        id=row.id,
        agent_id=row.agent_id,
        agent_name=agents.get(row.agent_id) if row.agent_id else None,
        llm_model_id=row.llm_model_id,
        model_label=models.get(row.llm_model_id) if row.llm_model_id else None,
        provider=row.provider,
        model_name=row.model_name,
        source=row.source,
        invocation_kind=row.invocation_kind,
        status=row.status,
        token_source=row.token_source,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_creation_tokens=row.cache_creation_tokens,
        total_tokens=row.total_tokens,
        estimated_tokens=row.estimated_tokens,
        duration_ms=row.duration_ms,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


async def _page(
    db: AsyncSession,
    *,
    statement,
    count_statement,
    page: int,
    page_size: int,
) -> tuple[int, list[AIInteractionSummaryOut]]:
    total_result = await db.execute(count_statement)
    total = int(total_result.scalar_one() or 0)
    result = await db.execute(
        statement.order_by(AIInteractionLog.started_at.desc(), AIInteractionLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(result.scalars().all())
    agents, models = await _names(db, rows)
    return total, [_summary(row, agents, models) for row in rows]


@router.get("/overview", response_model=AIInteractionOverviewOut)
async def ai_interaction_overview(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    since = datetime.now(UTC) - timedelta(hours=24)
    aggregates = await db.execute(
        select(
            func.count(AIInteractionLog.id),
            func.coalesce(func.sum(AIInteractionLog.total_tokens), 0),
            func.count(AIInteractionLog.id).filter(AIInteractionLog.status == "error"),
        ).where(AIInteractionLog.tenant_id == current_user.tenant_id, AIInteractionLog.created_at >= since)
    )
    calls, tokens, errors = aggregates.one()
    conditions = (AIInteractionLog.tenant_id == current_user.tenant_id,)
    total, interactions = await _page(
        db,
        statement=select(AIInteractionLog).where(*conditions),
        count_statement=select(func.count(AIInteractionLog.id)).where(*conditions),
        page=page,
        page_size=page_size,
    )
    return AIInteractionOverviewOut(
        calls_24h=int(calls or 0),
        errors_24h=int(errors or 0),
        total_tokens_24h=int(tokens or 0),
        page=page,
        page_size=page_size,
        total=total,
        interactions=interactions,
    )


@router.get("/groups/{group_id}/interactions", response_model=AIInteractionPageOut)
async def group_ai_interactions(
    group_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    """Return only calls whose recorded session belongs to this active group."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    conditions = (
        AIInteractionLog.tenant_id == current_user.tenant_id,
        ChatSession.tenant_id == current_user.tenant_id,
        ChatSession.group_id == group_id,
        ChatSession.session_type == "group",
        ChatSession.deleted_at.is_(None),
    )
    join = AIInteractionLog.session_id == cast(ChatSession.id, String)
    total, interactions = await _page(
        db,
        statement=select(AIInteractionLog).join(ChatSession, join).where(*conditions),
        count_statement=select(func.count(AIInteractionLog.id)).join(ChatSession, join).where(*conditions),
        page=page,
        page_size=page_size,
    )
    return AIInteractionPageOut(page=page, page_size=page_size, total=total, interactions=interactions)


@router.get("/interactions/{interaction_id}", response_model=AIInteractionDetailOut)
async def ai_interaction_detail(
    interaction_id: uuid.UUID,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    result = await db.execute(
        select(AIInteractionLog).where(
            AIInteractionLog.id == interaction_id,
            AIInteractionLog.tenant_id == current_user.tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI interaction was not found")
    agents, models = await _names(db, [row])
    return AIInteractionDetailOut(
        **_summary(row, agents, models).model_dump(),
        session_id=row.session_id,
        run_id=row.run_id,
        request_context=row.request_context,
        response_content=row.response_content,
        error=row.error,
    )
