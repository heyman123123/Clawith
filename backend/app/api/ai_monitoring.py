"""Administrator-only read API for privacy-safe AI interaction telemetry."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.models.agent import Agent
from app.models.ai_interaction import AIInteractionLog
from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.llm import LLMModel
from app.models.user import User

router = APIRouter(prefix="/api/ai-monitoring", tags=["ai-monitoring"])

SortBy = Literal["latest", "failures", "tokens", "calls"]
SortOrder = Literal["asc", "desc"]
RangeKey = Literal["24h"]


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


class AIAgentStatsRowOut(BaseModel):
    agent_id: uuid.UUID | None
    agent_name: str | None
    calls: int
    successes: int
    failures: int
    total_tokens: int
    last_called_at: datetime | None = None


class AIAgentStatsOut(BaseModel):
    range: str
    date: str | None
    since: datetime
    until: datetime
    sort_by: SortBy
    order: SortOrder
    group_id: uuid.UUID | None = None
    calls: int
    successes: int
    failures: int
    total_tokens: int
    agents: list[AIAgentStatsRowOut]


class AIGroupStatsRowOut(BaseModel):
    group_id: uuid.UUID | None
    group_name: str | None
    calls: int
    successes: int
    failures: int
    total_tokens: int
    last_called_at: datetime | None = None


class AIGroupStatsOut(BaseModel):
    range: str
    date: str | None
    since: datetime
    until: datetime
    sort_by: SortBy
    order: SortOrder
    calls: int
    successes: int
    failures: int
    total_tokens: int
    groups: list[AIGroupStatsRowOut]


def _metric_exprs():
    calls_expr = func.count(AIInteractionLog.id)
    failures_expr = func.count(AIInteractionLog.id).filter(AIInteractionLog.status == "error")
    successes_expr = func.count(AIInteractionLog.id).filter(AIInteractionLog.status == "success")
    tokens_expr = func.coalesce(func.sum(AIInteractionLog.total_tokens), 0)
    latest_expr = func.max(AIInteractionLog.created_at)
    return calls_expr, successes_expr, failures_expr, tokens_expr, latest_expr


def _ordered_metrics(*, sort_by: SortBy, order: SortOrder, latest_expr, failures_expr, tokens_expr, calls_expr):
    """Primary sort plus stable secondary ranks: latest → failures → tokens → calls."""
    primary = {
        "latest": latest_expr,
        "failures": failures_expr,
        "tokens": tokens_expr,
        "calls": calls_expr,
    }[sort_by]
    direction = "asc" if order == "asc" else "desc"
    def _dir(column):
        return column.asc().nulls_last() if direction == "asc" else column.desc().nulls_last()

    secondary = [latest_expr, failures_expr, tokens_expr, calls_expr]
    # Keep primary first, then the remaining default priority without duplicates.
    ordered_cols = [primary]
    for column in secondary:
        if column is not primary:
            ordered_cols.append(column)
    return [_dir(column) for column in ordered_cols]


def _session_group_join():
    return AIInteractionLog.session_id == cast(ChatSession.id, String)


def _active_group_session_filters(tenant_id: uuid.UUID):
    return (
        ChatSession.tenant_id == tenant_id,
        ChatSession.session_type == "group",
        ChatSession.deleted_at.is_(None),
        ChatSession.group_id.is_not(None),
    )


def _parse_day(value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date must be YYYY-MM-DD",
        ) from exc


def _window(
    *,
    range_key: RangeKey | None,
    day: date | None,
) -> tuple[datetime, datetime, str]:
    now = datetime.now(UTC)
    if day is not None:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        return start, end, "day"
    # Default and explicit 24h both use a rolling window.
    _ = range_key
    return now - timedelta(hours=24), now, "24h"


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
    agent_id: uuid.UUID | None = None,
    unassigned: bool = False,
    date: str | None = None,
    range_key: RangeKey | None = Query(default=None, alias="range"),  # noqa: B008
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    day = _parse_day(date)
    since, until, _ = _window(range_key=range_key, day=day)
    # Keep 24h cards stable for the dashboard headline even when drilling by day.
    rolling_since = datetime.now(UTC) - timedelta(hours=24)
    aggregates = await db.execute(
        select(
            func.count(AIInteractionLog.id),
            func.coalesce(func.sum(AIInteractionLog.total_tokens), 0),
            func.count(AIInteractionLog.id).filter(AIInteractionLog.status == "error"),
        ).where(
            AIInteractionLog.tenant_id == current_user.tenant_id,
            AIInteractionLog.created_at >= rolling_since,
        )
    )
    calls, tokens, errors = aggregates.one()
    conditions = [
        AIInteractionLog.tenant_id == current_user.tenant_id,
        AIInteractionLog.created_at >= since,
        AIInteractionLog.created_at < until,
    ]
    if unassigned:
        conditions.append(AIInteractionLog.agent_id.is_(None))
    elif agent_id is not None:
        conditions.append(AIInteractionLog.agent_id == agent_id)
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


@router.get("/agents/stats", response_model=AIAgentStatsOut)
async def ai_agent_stats(
    date: str | None = None,
    range_key: RangeKey | None = Query(default="24h", alias="range"),  # noqa: B008
    sort_by: SortBy = "latest",
    order: SortOrder = "desc",
    group_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    day = _parse_day(date)
    effective_range = None if day is not None else range_key
    since, until, range_label = _window(range_key=effective_range, day=day)
    calls_expr, successes_expr, failures_expr, tokens_expr, latest_expr = _metric_exprs()
    order_by = _ordered_metrics(
        sort_by=sort_by,
        order=order,
        latest_expr=latest_expr,
        failures_expr=failures_expr,
        tokens_expr=tokens_expr,
        calls_expr=calls_expr,
    )
    statement = (
        select(
            AIInteractionLog.agent_id,
            calls_expr,
            successes_expr,
            failures_expr,
            tokens_expr,
            latest_expr,
        )
        .where(
            AIInteractionLog.tenant_id == current_user.tenant_id,
            AIInteractionLog.created_at >= since,
            AIInteractionLog.created_at < until,
        )
        .group_by(AIInteractionLog.agent_id)
        .order_by(*order_by, AIInteractionLog.agent_id.asc().nulls_last())
    )
    if group_id is not None:
        statement = (
            select(
                AIInteractionLog.agent_id,
                calls_expr,
                successes_expr,
                failures_expr,
                tokens_expr,
                latest_expr,
            )
            .join(ChatSession, _session_group_join())
            .where(
                AIInteractionLog.tenant_id == current_user.tenant_id,
                AIInteractionLog.created_at >= since,
                AIInteractionLog.created_at < until,
                *_active_group_session_filters(current_user.tenant_id),
                ChatSession.group_id == group_id,
            )
            .group_by(AIInteractionLog.agent_id)
            .order_by(*order_by, AIInteractionLog.agent_id.asc().nulls_last())
        )
    result = await db.execute(statement)
    rows = list(result.all())
    agent_ids = {agent_id for agent_id, *_ in rows if agent_id is not None}
    names: dict[uuid.UUID, str] = {}
    if agent_ids:
        name_result = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
        names = dict(name_result.all())
    agents = [
        AIAgentStatsRowOut(
            agent_id=agent_id,
            agent_name=names.get(agent_id) if agent_id else None,
            calls=int(calls or 0),
            successes=int(successes or 0),
            failures=int(failures or 0),
            total_tokens=int(tokens or 0),
            last_called_at=last_called_at,
        )
        for agent_id, calls, successes, failures, tokens, last_called_at in rows
    ]
    return AIAgentStatsOut(
        range=range_label,
        date=day.isoformat() if day else None,
        since=since,
        until=until,
        sort_by=sort_by,
        order=order,
        group_id=group_id,
        calls=sum(row.calls for row in agents),
        successes=sum(row.successes for row in agents),
        failures=sum(row.failures for row in agents),
        total_tokens=sum(row.total_tokens for row in agents),
        agents=agents,
    )


@router.get("/groups/stats", response_model=AIGroupStatsOut)
async def ai_group_stats(
    date: str | None = None,
    range_key: RangeKey | None = Query(default="24h", alias="range"),  # noqa: B008
    sort_by: SortBy = "latest",
    order: SortOrder = "desc",
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    day = _parse_day(date)
    effective_range = None if day is not None else range_key
    since, until, range_label = _window(range_key=effective_range, day=day)
    calls_expr, successes_expr, failures_expr, tokens_expr, latest_expr = _metric_exprs()
    order_by = _ordered_metrics(
        sort_by=sort_by,
        order=order,
        latest_expr=latest_expr,
        failures_expr=failures_expr,
        tokens_expr=tokens_expr,
        calls_expr=calls_expr,
    )
    result = await db.execute(
        select(
            ChatSession.group_id,
            calls_expr,
            successes_expr,
            failures_expr,
            tokens_expr,
            latest_expr,
        )
        .join(ChatSession, _session_group_join())
        .where(
            AIInteractionLog.tenant_id == current_user.tenant_id,
            AIInteractionLog.created_at >= since,
            AIInteractionLog.created_at < until,
            *_active_group_session_filters(current_user.tenant_id),
        )
        .group_by(ChatSession.group_id)
        .order_by(*order_by, ChatSession.group_id.asc())
    )
    rows = list(result.all())
    group_ids = {group_id for group_id, *_ in rows if group_id is not None}
    names: dict[uuid.UUID, str] = {}
    if group_ids:
        name_result = await db.execute(
            select(Group.id, Group.name).where(
                Group.id.in_(group_ids),
                Group.tenant_id == current_user.tenant_id,
            )
        )
        names = dict(name_result.all())
    groups = [
        AIGroupStatsRowOut(
            group_id=group_id,
            group_name=names.get(group_id) if group_id else None,
            calls=int(calls or 0),
            successes=int(successes or 0),
            failures=int(failures or 0),
            total_tokens=int(tokens or 0),
            last_called_at=last_called_at,
        )
        for group_id, calls, successes, failures, tokens, last_called_at in rows
    ]
    return AIGroupStatsOut(
        range=range_label,
        date=day.isoformat() if day else None,
        since=since,
        until=until,
        sort_by=sort_by,
        order=order,
        calls=sum(row.calls for row in groups),
        successes=sum(row.successes for row in groups),
        failures=sum(row.failures for row in groups),
        total_tokens=sum(row.total_tokens for row in groups),
        groups=groups,
    )


@router.get("/groups/{group_id}/interactions", response_model=AIInteractionPageOut)
async def group_ai_interactions(
    group_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    agent_id: uuid.UUID | None = None,
    unassigned: bool = False,
    date: str | None = None,
    range_key: RangeKey | None = Query(default=None, alias="range"),  # noqa: B008
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    """Return only calls whose recorded session belongs to this active group."""
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is required")
    day = _parse_day(date)
    conditions = [
        AIInteractionLog.tenant_id == current_user.tenant_id,
        *_active_group_session_filters(current_user.tenant_id),
        ChatSession.group_id == group_id,
    ]
    if day is not None or range_key is not None:
        since, until, _ = _window(range_key=range_key, day=day)
        conditions.extend(
            [
                AIInteractionLog.created_at >= since,
                AIInteractionLog.created_at < until,
            ]
        )
    if unassigned:
        conditions.append(AIInteractionLog.agent_id.is_(None))
    elif agent_id is not None:
        conditions.append(AIInteractionLog.agent_id == agent_id)
    join = _session_group_join()
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
