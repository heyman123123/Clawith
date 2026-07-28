"""Evolution signal / draft / harness orchestration (P4).

This module sits between the quality engine and the patch engine. Each
time a workflow step passes the rule engine we want to:

1. Persist a ``AgentEvolutionSignal`` row capturing the verdict, judge
   payload and free-form reasons so we have a stable audit trail.
2. Optionally draft a new soul via
   :func:`app.services.ao.patch_engine.draft_patch_from_signals` and
   then run the regression harness.
3. Apply the draft via :func:`apply_with_gating` if the harness shows a
   strict lift above the gating threshold.

All operations are idempotent and best-effort; the quality engine must
not raise if any of these side-effects fail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.models.evolution import (
    AgentEvolutionDraft,
    AgentEvolutionSignal,
    AgentHarnessRun,
    AgentRoleVersion,
)


@dataclass(slots=True)
class DraftResult:
    draft_id: uuid.UUID | None
    baseline_score: int | None
    candidate_score: int | None
    applied: bool
    declined_reason: str | None


async def record_quality_signal(
    db,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    quality_score: int,
    rule_score: int | None,
    judge_payload: dict | None,
    trigger_ref_id: uuid.UUID | None = None,
    summary: str | None = None,
) -> AgentEvolutionSignal | None:
    """Persist a single quality-passed signal for later aggregation.

    Returns the persisted row, or ``None`` if persistence failed (we log
    but never raise — quality checks must not abort because of the P4
    audit log).
    """
    try:
        reasons = []
        if isinstance(judge_payload, dict):
            raw = judge_payload.get("reasons")
            if isinstance(raw, list):
                reasons = [str(item) for item in raw if item]
        signal = AgentEvolutionSignal(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            kind="judge_flagged" if judge_payload and judge_payload.get("judge_used") else "quality_passed",
            trigger_source="ao_quality_check",
            trigger_ref_id=trigger_ref_id,
            quality_score=quality_score,
            rule_score=rule_score,
            judge_used=bool(judge_payload and judge_payload.get("judge_used")),
            judge_payload=judge_payload,
            reasons=reasons,
            summary=summary,
        )
        db.add(signal)
        await db.flush()
        return signal
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[EvolutionSignal] persist failed for agent={}: {}",
            agent_id,
            exc,
        )
        return None


async def recent_signals_for_agent(
    db,
    *,
    agent_id: uuid.UUID,
    limit: int = 32,
) -> list[AgentEvolutionSignal]:
    rows = (
        await db.execute(
            select(AgentEvolutionSignal)
            .where(AgentEvolutionSignal.agent_id == agent_id)
            .order_by(AgentEvolutionSignal.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def store_draft(
    db,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    baseline_version_id: uuid.UUID | None,
    patch_strategy: str,
    rationale: str | None,
    rule_additions: list[dict[str, Any]],
    draft_soul_md: str | None,
    source_signal_ids: list[uuid.UUID],
    status: str = "pending",
    decline_reason: str | None = None,
) -> AgentEvolutionDraft:
    draft = AgentEvolutionDraft(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        baseline_version_id=baseline_version_id,
        patch_strategy=patch_strategy,
        rationale=rationale,
        rule_additions=rule_additions,
        draft_soul_md=draft_soul_md,
        status=status,
        decline_reason=decline_reason,
        source_signal_ids=[str(s) for s in source_signal_ids],
    )
    db.add(draft)
    await db.flush()
    return draft


async def finalize_draft(
    db,
    *,
    draft_id: uuid.UUID,
    status: str,
    decline_reason: str | None = None,
) -> AgentEvolutionDraft | None:
    draft = await db.scalar(
        select(AgentEvolutionDraft).where(AgentEvolutionDraft.id == draft_id)
    )
    if draft is None:
        return None
    draft.status = status
    draft.decline_reason = decline_reason
    await db.flush()
    return draft


async def store_harness_run(
    db,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    draft_id: uuid.UUID,
    stage: str,
    per_fixture: list[dict[str, Any]],
    status: str = "succeeded",
    error: str | None = None,
) -> AgentHarnessRun:
    scores = [
        int(item.get("score", 0))
        for item in per_fixture
        if isinstance(item, dict) and item.get("score") is not None
    ]
    average = round(sum(scores) / len(scores)) if scores else 0
    passed = sum(1 for s in scores if s >= 80)
    failed = sum(1 for s in scores if s < 80)
    from datetime import UTC, datetime

    run = AgentHarnessRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        draft_id=draft_id,
        stage=stage,
        status=status,
        average_score=average,
        passed_count=passed,
        failed_count=failed,
        fixture_count=len(per_fixture),
        per_fixture=per_fixture,
        error=error,
        completed_at=datetime.now(UTC) if status != "running" else None,
    )
    db.add(run)
    await db.flush()
    return run


async def latest_baseline_version(
    db,
    *,
    agent_id: uuid.UUID,
) -> AgentRoleVersion | None:
    return await db.scalar(
        select(AgentRoleVersion)
        .where(
            AgentRoleVersion.agent_id == agent_id,
            AgentRoleVersion.is_current.is_(True),
        )
    )


async def get_draft(
    db,
    *,
    draft_id: uuid.UUID,
) -> AgentEvolutionDraft | None:
    return await db.scalar(
        select(AgentEvolutionDraft).where(AgentEvolutionDraft.id == draft_id)
    )


__all__ = [
    "DraftResult",
    "finalize_draft",
    "get_draft",
    "latest_baseline_version",
    "recent_signals_for_agent",
    "record_quality_signal",
    "store_draft",
    "store_harness_run",
]
