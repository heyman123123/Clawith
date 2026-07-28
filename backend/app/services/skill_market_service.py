"""Skill marketplace service (P5).

Backs the public ``/api/skill-market`` endpoints with marketplace
listings, sandbox smoke-tests, high-risk approvals and learning
records. The execution half is intentionally thin: it calls the
``sandbox`` registry that already drives ``execute_code``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill, SkillFile
from app.models.skill_market import (
    AgentSkillBinding,
    SkillApprovalRequest,
    SkillLearningRecord,
    SkillMarketListing,
    SkillSandboxRun,
)
from app.services.sandbox_risk import (
    RiskAssessment,
    assess_risk,
    combine_risk,
    should_auto_publish,
)

# ---------------------------------------------------------------------------
# Result / payload dataclasses (pure, no ORM)
# ---------------------------------------------------------------------------


@dataclass
class OfflineExecution:
    """Fallback stand-in when the sandbox backend raises at construction time."""

    success: bool = False
    exit_code: int = 1
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    def __init__(
        self,
        *,
        error: str,
        stderr: str = "",
        stdout: str = "",
        duration_ms: int = 0,
    ) -> None:
        self.error = error
        self.stderr = stderr
        self.stdout = stdout
        self.duration_ms = duration_ms


@dataclass
class ListingPayload:
    listing_id: str
    skill_id: str
    title: str
    summary: str
    keywords: list[str]
    risk_level: str
    status: str
    share_scope: str
    install_count: int
    published_at: datetime | None

    def to_dict(self) -> dict:
        return {
            "listing_id": self.listing_id,
            "skill_id": self.skill_id,
            "title": self.title,
            "summary": self.summary,
            "keywords": list(self.keywords),
            "risk_level": self.risk_level,
            "status": self.status,
            "share_scope": self.share_scope,
            "install_count": self.install_count,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


@dataclass
class SandboxResult:
    run_id: str
    status: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    detected_risk_level: str
    risk_assessment: RiskAssessment
    error: str | None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:1000],
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "detected_risk_level": self.detected_risk_level,
            "risk_assessment": self.risk_assessment.to_dict(),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Listing CRUD (no DB calls in the hot path)
# ---------------------------------------------------------------------------


def _listing_payload(row: SkillMarketListing) -> ListingPayload:
    return ListingPayload(
        listing_id=str(row.id),
        skill_id=str(row.skill_id),
        title=row.title,
        summary=row.summary or "",
        keywords=list(row.keywords or []),
        risk_level=row.risk_level,
        status=row.status,
        share_scope=row.share_scope,
        install_count=row.install_count,
        published_at=row.published_at,
    )


async def create_listing(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    skill_id: uuid.UUID,
    title: str,
    summary: str = "",
    keywords: Iterable[str] = (),
    risk_level: str = "low",
    share_scope: str = "team",
    publisher_user_id: uuid.UUID | None = None,
    files_snapshot: list[tuple[str, str]] | None = None,
) -> SkillMarketListing:
    """Idempotent listing create. Reuses existing skill if ``files_snapshot`` is absent."""

    existing = await session.scalar(
        select(SkillMarketListing).where(SkillMarketListing.skill_id == skill_id)
    )
    if existing is not None:
        return existing

    skill = await session.get(Skill, skill_id)
    if skill is None:
        skill = Skill(
            id=skill_id,
            tenant_id=tenant_id,
            name=(title or "skill").lower().replace(" ", "-")[:96],
            description=summary,
            folder_name=f"{title or 'skill'}_{uuid.uuid4().hex[:6]}",
            is_builtin=False,
            is_default=False,
        )
        session.add(skill)
        await session.flush()
        if files_snapshot:
            for path, content in files_snapshot:
                session.add(SkillFile(skill_id=skill_id, path=path, content=content))

    listing = SkillMarketListing(
        tenant_id=tenant_id,
        skill_id=skill_id,
        title=title,
        summary=summary,
        keywords=list(keywords),
        risk_level=risk_level,
        share_scope=share_scope,
        status="in_review" if not should_auto_publish(risk_level) else "published",
        publisher_user_id=publisher_user_id,
        published_at=datetime.utcnow() if risk_level == "low" else None,
    )
    session.add(listing)
    await session.flush()
    return listing


async def publish_listing(
    session: AsyncSession, listing_id: uuid.UUID
) -> SkillMarketListing:
    """Flip ``status`` to ``published`` and stamp ``published_at``."""
    listing = await session.get(SkillMarketListing, listing_id)
    if listing is None:
        raise LookupError(f"listing {listing_id} not found")
    if listing.status == "published":
        return listing
    listing.status = "published"
    listing.published_at = listing.published_at or datetime.utcnow()
    await session.flush()
    return listing


async def disable_listing(
    session: AsyncSession, listing_id: uuid.UUID
) -> SkillMarketListing:
    listing = await session.get(SkillMarketListing, listing_id)
    if listing is None:
        raise LookupError(f"listing {listing_id} not found")
    listing.status = "disabled"
    await session.flush()
    return listing


async def list_market(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: str | None = "published",
    limit: int = 50,
) -> list[SkillMarketListing]:
    stmt = select(SkillMarketListing).where(SkillMarketListing.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(SkillMarketListing.status == status)
    stmt = stmt.order_by(SkillMarketListing.created_at.desc()).limit(limit)
    rows = (await session.scalars(stmt)).all()
    return list(rows)


def listings_to_payload(rows: Iterable[SkillMarketListing]) -> list[dict]:
    return [_listing_payload(r).to_dict() for r in rows]


# ---------------------------------------------------------------------------
# Sandbox + risk routing
# ---------------------------------------------------------------------------


def _truncate(value: str | None, limit: int) -> str:
    if not value:
        return ""
    return value if len(value) <= limit else value[:limit]


async def run_skill_smoke_test(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    skill_id: uuid.UUID,
    listing_id: uuid.UUID | None,
    language: str,
    code: str,
    timeout: int = 30,
    work_dir: str | None = None,
    triggered_by_user_id: uuid.UUID | None = None,
    allow_network: bool = False,
    sandbox_factory=None,
) -> SandboxResult:
    """Persist a :class:`SkillSandboxRun`, run the code, return result.

    ``sandbox_factory`` is the caller's hook for swapping in a stub
    backend for tests — defaults to the production :mod:`SandboxConfig`
    pipeline.
    """

    assessment = assess_risk(code, allow_network=allow_network)
    run_row = SkillSandboxRun(
        tenant_id=tenant_id,
        listing_id=listing_id,
        skill_id=skill_id,
        triggered_by_user_id=triggered_by_user_id,
        language=language,
        code_excerpt=_truncate(code, 4000),
        status="running",
        detected_risk_level=assessment.risk_level,
    )
    session.add(run_row)
    await session.flush()

    backend = _resolve_backend(sandbox_factory)
    try:
        outcome = await backend.execute(code=code, language=language, timeout=timeout, work_dir=work_dir)
    except Exception as exc:  # noqa: BLE001 - defensive, surface error to caller
        logger.warning("sandbox backend raised: {}", exc)
        outcome = _offline_outcome(exc)

    merged = combine_risk(
        assessment,
        _outcome_risk(outcome),
    )
    run_row.status = _map_status(outcome)
    run_row.exit_code = outcome.exit_code
    run_row.duration_ms = outcome.duration_ms
    run_row.stdout = _truncate(outcome.stdout, 8000)
    run_row.stderr = _truncate(outcome.stderr, 4000)
    run_row.error = outcome.error
    run_row.detected_risk_level = merged.risk_level
    await session.flush()

    return SandboxResult(
        run_id=str(run_row.id),
        status=run_row.status,
        stdout=run_row.stdout,
        stderr=run_row.stderr,
        exit_code=run_row.exit_code,
        duration_ms=run_row.duration_ms,
        detected_risk_level=merged.risk_level,
        risk_assessment=merged,
        error=run_row.error,
    )


def _resolve_backend(sandbox_factory):
    if sandbox_factory is not None:
        return sandbox_factory()
    # Production path: route through the registry but use subprocess as
    # the safe default. Higher-risk skills will be flagged before this
    # ever runs.
    from app.services.sandbox.config import SandboxConfig
    from app.services.sandbox.local.subprocess_backend import SubprocessBackend

    config = SandboxConfig.from_dict({"sandbox_type": "subprocess"})
    return SubprocessBackend(config)


def _offline_outcome(exc: Exception) -> OfflineExecution:
    """Build an :class:`OfflineExecution` when a backend raises at construction."""
    return OfflineExecution(
        error=f"sandbox backend unavailable: {exc}",
        stderr=str(exc),
    )


def _outcome_risk(outcome) -> RiskAssessment:
    if getattr(outcome, "error", None) and "Blocked" in outcome.error:
        return RiskAssessment(
            risk_level="medium",
            detected_patterns=("safety_block",),
            requires_human_review=False,
            rationale="sandbox safety guard blocked the run",
        )
    if getattr(outcome, "error", None) and "timed out" in outcome.error:
        return RiskAssessment(
            risk_level="medium",
            detected_patterns=("timeout",),
            requires_human_review=False,
            rationale="execution exceeded the timeout",
        )
    if getattr(outcome, "success", False):
        return RiskAssessment(
            risk_level="low",
            detected_patterns=(),
            requires_human_review=False,
            rationale="execution succeeded",
        )
    return RiskAssessment(
        risk_level="medium",
        detected_patterns=("execution_failed",),
        requires_human_review=False,
        rationale="execution failed",
    )


def _map_status(outcome) -> str:
    if getattr(outcome, "success", False):
        return "succeeded"
    err = getattr(outcome, "error", None) or ""
    if "timed out" in err:
        return "timeout"
    if "Blocked" in err:
        return "blocked"
    return "failed"


# ---------------------------------------------------------------------------
# Approval flow
# ---------------------------------------------------------------------------


async def open_approval(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    listing_id: uuid.UUID,
    sandbox_run_id: uuid.UUID | None,
    requester_user_id: uuid.UUID | None,
    rationale: str | None = None,
    kind: str = "high_risk_publish",
) -> SkillApprovalRequest:
    row = SkillApprovalRequest(
        tenant_id=tenant_id,
        listing_id=listing_id,
        sandbox_run_id=sandbox_run_id,
        kind=kind,
        decision="pending",
        requester_user_id=requester_user_id,
        rationale=rationale,
    )
    session.add(row)
    await session.flush()
    return row


async def resolve_approval(
    session: AsyncSession,
    approval_id: uuid.UUID,
    *,
    decision: str,
    reviewer_user_id: uuid.UUID,
    decision_notes: str | None = None,
) -> SkillApprovalRequest:
    if decision not in {"approved", "rejected"}:
        raise ValueError(f"invalid decision {decision!r}")
    row = await session.get(SkillApprovalRequest, approval_id)
    if row is None:
        raise LookupError(f"approval {approval_id} not found")
    row.decision = decision
    row.reviewer_user_id = reviewer_user_id
    row.decision_notes = decision_notes
    row.resolved_at = datetime.utcnow()
    if decision == "approved":
        await publish_listing(session, row.listing_id)
        listing = await session.get(SkillMarketListing, row.listing_id)
        if listing:
            listing.status = "published"
    elif decision == "rejected":
        listing = await session.get(SkillMarketListing, row.listing_id)
        if listing:
            listing.status = "rejected"
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Skill learning (single end-to-end helper)
# ---------------------------------------------------------------------------


async def start_learning(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    trigger_reason: str,
    detected_gap: str | None,
    skill_id: uuid.UUID,
    listing_id: uuid.UUID | None,
    risk_level: str = "low",
) -> SkillLearningRecord:
    record = SkillLearningRecord(
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        listing_id=listing_id,
        trigger_reason=trigger_reason,
        detected_gap=detected_gap,
        detected_risk_level=risk_level,
        status="bound" if risk_level == "low" else "awaiting_approval",
    )
    session.add(record)
    await session.flush()
    if risk_level == "low":
        session.add(
            AgentSkillBinding(
                tenant_id=tenant_id,
                agent_id=agent_id,
                skill_id=skill_id,
                learning_record_id=record.id,
            )
        )
        await session.flush()
        record.status = "bound"
        record.completed_at = datetime.utcnow()
        await session.flush()
    return record


async def complete_learning(
    session: AsyncSession,
    record_id: uuid.UUID,
    *,
    success: bool,
    failure_reason: str | None = None,
) -> SkillLearningRecord:
    record = await session.get(SkillLearningRecord, record_id)
    if record is None:
        raise LookupError(f"learning record {record_id} not found")
    if success:
        session.add(
            AgentSkillBinding(
                tenant_id=record.tenant_id,
                agent_id=record.agent_id,
                skill_id=record.skill_id,
                learning_record_id=record.id,
            )
        )
        record.status = "bound"
    else:
        record.status = "rejected" if record.detected_risk_level == "high" else "failed"
        record.failure_reason = failure_reason
    record.completed_at = datetime.utcnow()
    await session.flush()
    return record


async def agent_enabled_skills(
    session: AsyncSession, agent_id: uuid.UUID
) -> list[Skill]:
    stmt = (
        select(Skill)
        .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
        .where(
            AgentSkillBinding.agent_id == agent_id,
            AgentSkillBinding.is_enabled.is_(True),
        )
    )
    return list((await session.scalars(stmt)).all())


def serialize_learning(record: SkillLearningRecord) -> dict:
    return {
        "record_id": str(record.id),
        "agent_id": str(record.agent_id),
        "skill_id": str(record.skill_id) if record.skill_id else None,
        "listing_id": str(record.listing_id) if record.listing_id else None,
        "status": record.status,
        "detected_risk_level": record.detected_risk_level,
        "trigger_reason": record.trigger_reason,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "failure_reason": record.failure_reason,
    }


def json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = [
    "ListingPayload",
    "SandboxResult",
    "agent_enabled_skills",
    "complete_learning",
    "create_listing",
    "disable_listing",
    "json_dump",
    "list_market",
    "listings_to_payload",
    "open_approval",
    "publish_listing",
    "resolve_approval",
    "run_skill_smoke_test",
    "serialize_learning",
    "start_learning",
    "OfflineExecution",
]
