"""Daily metrics aggregation + template-matching service (P6).

Two responsibilities:

1. :func:`aggregate_daily_metrics` materializes one
   :class:`WorkflowMetricDaily` row per tenant per day. Called both
   online (during workflow lifecycle hooks) and via the nightly
   backfill job.

2. :func:`match_top_templates` ranks the curated
   :class:`WorkflowTemplate` catalog against a requirements excerpt.

The pure helpers here are intentionally framework-light so we can test
them without a session.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metrics import (
    WorkflowMetricDaily,
    WorkflowTemplate,
    WorkflowTemplateMatchEvent,
)
from app.models.workflow_run import WorkflowRun, WorkflowRunStep

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[\w+#\-\.]+")


def tokenize(text: str | None) -> set[str]:
    """Lower-case word tokenisation that survives CJK / ASCII mixes."""
    if not text:
        return set()
    lower = text.lower()
    out: set[str] = set()
    for match in _TOKEN_RE.findall(lower):
        if len(match) > 1:
            out.add(match)
    for ch in lower:
        if "一" <= ch <= "鿿":
            out.add(ch)
    return out


@dataclass
class TemplateMatch:
    template_id: str
    slug: str
    title: str
    summary: str
    score: float
    matched_keywords: list[str]
    rank: int

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "score": round(self.score, 4),
            "matched_keywords": list(self.matched_keywords),
            "rank": self.rank,
        }


def _score_pair(req_tokens: set[str], template: WorkflowTemplate) -> tuple[float, list[str]]:
    """Score a single template against the requirement token set."""

    if not req_tokens:
        return 0.0, []
    keywords = list(template.keywords or [])
    tags = list(template.tags or [])
    kw_tokens = tokenize(" ".join(keywords + tags))
    title_tokens = tokenize(" ".join([template.title, template.summary]))
    matched = sorted(req_tokens & (kw_tokens | title_tokens))
    if not kw_tokens and not title_tokens:
        return 0.0, []
    score = len(matched) / max(len(req_tokens), 1)
    # Boost for tag overlap (tag overlaps mean explicit alignment).
    if tags:
        tag_matches = req_tokens & tokenize(" ".join(tags))
        score += 0.25 * (len(tag_matches) / max(len(tags), 1))
    return min(score, 1.0), matched


def rank_top_n(
    requirements: str,
    candidates: Sequence[WorkflowTemplate],
    *,
    top_n: int = 3,
    min_score: float = 0.05,
) -> list[TemplateMatch]:
    req_tokens = tokenize(requirements)
    scored: list[TemplateMatch] = []
    for template in candidates:
        score, matched = _score_pair(req_tokens, template)
        if score >= min_score:
            scored.append(
                TemplateMatch(
                    template_id=str(template.id),
                    slug=template.slug,
                    title=template.title,
                    summary=template.summary or "",
                    score=score,
                    matched_keywords=matched,
                    rank=0,
                )
            )
    scored.sort(key=lambda m: (m.score, len(m.matched_keywords)), reverse=True)
    for idx, match in enumerate(scored[:top_n]):
        match.rank = idx + 1
    return scored[:top_n]


def _metric_defaults() -> dict[str, float | int]:
    return {
        "workflows_started": 0,
        "workflows_succeeded": 0,
        "workflows_failed": 0,
        "steps_dispatched": 0,
        "steps_quality_passed": 0,
        "steps_quality_failed": 0,
        "steps_delivery_approved": 0,
        "steps_delivery_rejected": 0,
        "sandbox_runs_total": 0,
        "sandbox_runs_blocked": 0,
        "skill_learning_total": 0,
        "skill_learning_approved": 0,
        "skill_learning_rejected": 0,
        "evolution_events": 0,
        "evolution_rollbacks": 0,
        "tokens_input_total": 0,
        "tokens_output_total": 0,
        "quality_score_avg": 0.0,
    }


@dataclass
class DailyAggregationResult:
    tenant_id: str
    metric_date: date
    fields: dict[str, float]
    rows_updated: bool

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "metric_date": self.metric_date.isoformat(),
            "rows_updated": self.rows_updated,
            **self.fields,
        }


# ---------------------------------------------------------------------------
# Metrics aggregation
# ---------------------------------------------------------------------------


async def get_or_create_daily_row(
    session: AsyncSession, tenant_id: uuid.UUID, day: date
) -> WorkflowMetricDaily:
    row = await session.scalar(
        select(WorkflowMetricDaily).where(
            WorkflowMetricDaily.tenant_id == tenant_id,
            WorkflowMetricDaily.metric_date == day,
        )
    )
    if row is not None:
        return row
    row = WorkflowMetricDaily(tenant_id=tenant_id, metric_date=day)
    session.add(row)
    await session.flush()
    return row


def _merge(row: WorkflowMetricDaily, updates: dict[str, float]) -> None:
    for key, value in updates.items():
        setattr(row, key, getattr(row, key) + value)


async def aggregate_daily_metrics(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    day: date | None = None,
    *,
    # Test hooks: callers may pre-compute any of these to bypass the
    # expensive session queries (e.g. unit tests, dry-runs).
    workflows_started: int = 0,
    workflows_succeeded: int = 0,
    workflows_failed: int = 0,
    steps_dispatched: int = 0,
    steps_quality_passed: int = 0,
    steps_quality_failed: int = 0,
    steps_delivery_approved: int = 0,
    steps_delivery_rejected: int = 0,
    sandbox_runs_total: int = 0,
    sandbox_runs_blocked: int = 0,
    skill_learning_total: int = 0,
    skill_learning_approved: int = 0,
    skill_learning_rejected: int = 0,
    evolution_events: int = 0,
    evolution_rollbacks: int = 0,
    tokens_input_total: int = 0,
    tokens_output_total: int = 0,
    quality_score_avg: float = 0.0,
    compute_online: bool = True,
) -> DailyAggregationResult:
    """Aggregate :class:`WorkflowMetricDaily` for ``(tenant_id, day)``.

    When ``compute_online`` is True (default), counts are sourced from
    the live DB if all counters are zero. Otherwise the explicit kwargs
    are used (useful for backfill jobs and tests).
    """

    day = day or date.today()

    if compute_online and not any(
        [
            workflows_started,
            workflows_succeeded,
            workflows_failed,
            steps_dispatched,
            steps_quality_passed,
            steps_quality_failed,
            steps_delivery_approved,
            steps_delivery_rejected,
            sandbox_runs_total,
            sandbox_runs_blocked,
            skill_learning_total,
            skill_learning_approved,
            skill_learning_rejected,
            evolution_events,
            evolution_rollbacks,
            tokens_input_total,
            tokens_output_total,
            quality_score_avg,
        ]
    ):
        live = await _online_fetch(session, tenant_id, day)
        workflows_started = live.get("workflows_started", 0)
        workflows_succeeded = live.get("workflows_succeeded", 0)
        workflows_failed = live.get("workflows_failed", 0)
        steps_dispatched = live.get("steps_dispatched", 0)
        steps_quality_passed = live.get("steps_quality_passed", 0)
        steps_quality_failed = live.get("steps_quality_failed", 0)
        steps_delivery_approved = live.get("steps_delivery_approved", 0)
        steps_delivery_rejected = live.get("steps_delivery_rejected", 0)
        sandbox_runs_total = live.get("sandbox_runs_total", 0)
        sandbox_runs_blocked = live.get("sandbox_runs_blocked", 0)
        skill_learning_total = live.get("skill_learning_total", 0)
        skill_learning_approved = live.get("skill_learning_approved", 0)
        skill_learning_rejected = live.get("skill_learning_rejected", 0)
        evolution_events = live.get("evolution_events", 0)
        evolution_rollbacks = live.get("evolution_rollbacks", 0)
        tokens_input_total = live.get("tokens_input_total", 0)
        tokens_output_total = live.get("tokens_output_total", 0)
        quality_score_avg = live.get("quality_score_avg", 0.0)

    row = await get_or_create_daily_row(session, tenant_id, day)
    updates = {
        "workflows_started": workflows_started,
        "workflows_succeeded": workflows_succeeded,
        "workflows_failed": workflows_failed,
        "steps_dispatched": steps_dispatched,
        "steps_quality_passed": steps_quality_passed,
        "steps_quality_failed": steps_quality_failed,
        "steps_delivery_approved": steps_delivery_approved,
        "steps_delivery_rejected": steps_delivery_rejected,
        "sandbox_runs_total": sandbox_runs_total,
        "sandbox_runs_blocked": sandbox_runs_blocked,
        "skill_learning_total": skill_learning_total,
        "skill_learning_approved": skill_learning_approved,
        "skill_learning_rejected": skill_learning_rejected,
        "evolution_events": evolution_events,
        "evolution_rollbacks": evolution_rollbacks,
        "tokens_input_total": tokens_input_total,
        "tokens_output_total": tokens_output_total,
        "quality_score_avg": quality_score_avg,
    }
    if updates["steps_quality_passed"] or updates["steps_quality_failed"]:
        # Re-derive the average from cumulative values if we know both.
        existing_avg = float(getattr(row, "quality_score_avg", 0.0))
        existing_passed = int(getattr(row, "steps_quality_passed", 0))
        existing_failed = int(getattr(row, "steps_quality_failed", 0))
        total_prev = existing_passed + existing_failed
        total_now = total_prev + steps_quality_passed + steps_quality_failed
        if total_now:
            blended = (
                existing_avg * total_prev
                + quality_score_avg * (steps_quality_passed + steps_quality_failed)
            ) / total_now
            updates["quality_score_avg"] = blended

    _merge(row, updates)
    await session.flush()

    return DailyAggregationResult(
        tenant_id=str(tenant_id),
        metric_date=day,
        fields={**_metric_defaults(), **updates},
        rows_updated=True,
    )


async def _online_fetch(
    session: AsyncSession, tenant_id: uuid.UUID, day: date
) -> dict[str, float]:
    """Compute fresh metrics for the supplied day."""

    # Use ``created_at`` day-truncation. PostgreSQL truncates by ::date,
    # SQLite stores DateTime. We rely on the >= and < guards.
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    out: dict[str, float] = {}
    out["workflows_started"] = int(
        await session.scalar(
            select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.created_at >= start,
                WorkflowRun.created_at < end,
            )
        )
        or 0
    )
    out["workflows_succeeded"] = int(
        await session.scalar(
            select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.status == "succeeded",
                WorkflowRun.updated_at >= start,
                WorkflowRun.updated_at < end,
            )
        )
        or 0
    )
    out["workflows_failed"] = int(
        await session.scalar(
            select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.status == "failed",
                WorkflowRun.updated_at >= start,
                WorkflowRun.updated_at < end,
            )
        )
        or 0
    )
    out["steps_dispatched"] = int(
        await session.scalar(
            select(func.count()).select_from(WorkflowRunStep).where(
                WorkflowRunStep.tenant_id == tenant_id,
                WorkflowRunStep.created_at >= start,
                WorkflowRunStep.created_at < end,
            )
        )
        or 0
    )
    return out


async def list_metrics(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int = 14,
) -> list[WorkflowMetricDaily]:
    stmt = (
        select(WorkflowMetricDaily)
        .where(WorkflowMetricDaily.tenant_id == tenant_id)
        .order_by(WorkflowMetricDaily.metric_date.desc())
        .limit(days)
    )
    return list((await session.scalars(stmt)).all())


async def backfill_recent(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int = 7,
) -> list[DailyAggregationResult]:
    """Recompute metrics for each of the last ``days`` days."""

    today = date.today()
    results: list[DailyAggregationResult] = []
    for offset in range(days):
        target_day = today - timedelta(days=offset)
        try:
            results.append(
                await aggregate_daily_metrics(
                    session, tenant_id, day=target_day, compute_online=True
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "metrics backfill failed for tenant={} day={}: {}",
                tenant_id,
                target_day,
                exc,
            )
    return results


# ---------------------------------------------------------------------------
# Dashboard payload
# ---------------------------------------------------------------------------


def dashboard_payload(rows: Iterable[WorkflowMetricDaily]) -> dict:
    rows = sorted(rows, key=lambda r: r.metric_date)
    dates = [r.metric_date.isoformat() for r in rows]
    return {
        "dates": dates,
        "efficiency": {
            "workflows_started": [int(r.workflows_started) for r in rows],
            "workflows_succeeded": [int(r.workflows_succeeded) for r in rows],
            "workflows_failed": [int(r.workflows_failed) for r in rows],
            "steps_dispatched": [int(r.steps_dispatched) for r in rows],
        },
        "quality": {
            "steps_quality_passed": [int(r.steps_quality_passed) for r in rows],
            "steps_quality_failed": [int(r.steps_quality_failed) for r in rows],
            "steps_delivery_approved": [int(r.steps_delivery_approved) for r in rows],
            "steps_delivery_rejected": [int(r.steps_delivery_rejected) for r in rows],
            "quality_score_avg": [
                float(r.quality_score_avg or 0.0) for r in rows
            ],
        },
        "evolution": {
            "evolution_events": [int(r.evolution_events) for r in rows],
            "evolution_rollbacks": [int(r.evolution_rollbacks) for r in rows],
        },
        "skill": {
            "skill_learning_total": [int(r.skill_learning_total) for r in rows],
            "skill_learning_approved": [int(r.skill_learning_approved) for r in rows],
            "skill_learning_rejected": [int(r.skill_learning_rejected) for r in rows],
            "sandbox_runs_total": [int(r.sandbox_runs_total) for r in rows],
            "sandbox_runs_blocked": [int(r.sandbox_runs_blocked) for r in rows],
        },
        "cost": {
            "tokens_input_total": [int(r.tokens_input_total) for r in rows],
            "tokens_output_total": [int(r.tokens_output_total) for r in rows],
        },
    }


# ---------------------------------------------------------------------------
# Template matching
# ---------------------------------------------------------------------------


async def get_active_templates(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[WorkflowTemplate]:
    stmt = (
        select(WorkflowTemplate)
        .where(
            WorkflowTemplate.tenant_id == tenant_id,
            WorkflowTemplate.status == "published",
        )
        .order_by(WorkflowTemplate.usage_count.desc(), WorkflowTemplate.created_at.desc())
    )
    return list((await session.scalars(stmt)).all())


async def match_top_templates(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    requirements: str,
    *,
    top_n: int = 3,
    actor_user_id: uuid.UUID | None = None,
    requirements_excerpt_size: int = 600,
    min_score: float = 0.05,
) -> list[TemplateMatch]:
    candidates = await get_active_templates(session, tenant_id)
    matches = rank_top_n(
        requirements,
        candidates,
        top_n=top_n,
        min_score=min_score,
    )
    excerpt = (requirements or "")[:requirements_excerpt_size]
    for match in matches:
        session.add(
            WorkflowTemplateMatchEvent(
                tenant_id=tenant_id,
                template_id=uuid.UUID(match.template_id),
                requirements_excerpt=excerpt,
                match_score=match.score,
                rank=match.rank,
                outcome="presented",
                actor_user_id=actor_user_id,
            )
        )
    await session.flush()
    return matches


__all__ = [
    "DailyAggregationResult",
    "TemplateMatch",
    "aggregate_daily_metrics",
    "backfill_recent",
    "dashboard_payload",
    "get_active_templates",
    "list_metrics",
    "match_top_templates",
    "rank_top_n",
    "tokenize",
]
