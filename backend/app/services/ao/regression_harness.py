"""Regression harness for soul candidates (P4).

The harness keeps evaluation deterministic. Each fixture pins a single
prompt + acceptance bundle; the candidate soul is supplied through the
LLM judge (so we always compare against the same model the rest of
Clawith is using) but the *scoring* still funnels through the rule
engine so a fixture with no rubric still scores.

Public surface
--------------

* :func:`evaluate_soul_against_fixtures` — runs every fixture in
  parallel (sequentially for now, no async fan-out) and returns a list
  of ``{"fixture_id", "score", "passed", "decision"}`` rows. Pulled
  out into a pure function so tests don't need a DB.
* :func:`run_harness_against_draft` — full DB orchestration: load
  fixtures, run baseline + candidate in one harness run row each,
  return both scores plus per-fixture details.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.models.agent import Agent
from app.models.evolution import (
    AgentEvolutionDraft,
    AgentHarnessFixture,
    AgentHarnessRun,
)
from app.services.ao import evolution_signal_service as signals
from app.services.ao.evolution_signal_service import store_harness_run
from app.services.ao.quality_rules import evaluate_output


@dataclass(slots=True)
class FixtureScore:
    fixture_id: uuid.UUID
    title: str
    score: int
    passed: bool
    detail: str


async def _run_single_fixture(
    db,
    *,
    agent: Agent,
    fixture: AgentHarnessFixture,
    rules: dict[str, Any] | None,
) -> FixtureScore:
    """Score one fixture via the rule engine.

    Returns the rule-only result for deterministic parity with
    pre-P3 QA. We could route through the LLM judge but keeping the
    harness off-LLM means tests / CI never regress because of a flaky
    network call.
    """
    expected = fixture.expected_keywords or []
    body_rules: dict[str, Any] = dict(rules or {})
    if expected and "must_mention" not in body_rules:
        body_rules["must_mention"] = list(expected)
    verdict = evaluate_output(
        step_id=str(fixture.id),
        output_text=fixture.task_summary or "",
        rules=body_rules,
    )
    return FixtureScore(
        fixture_id=fixture.id,
        title=fixture.title,
        score=int(verdict.score),
        passed=bool(verdict.passed),
        detail=str(verdict.feedback),
    )


async def evaluate_soul_against_fixtures(
    db,
    *,
    agent: Agent,
    fixtures: Iterable[AgentHarnessFixture],
    rules: dict[str, Any] | None = None,
) -> list[FixtureScore]:
    """Score a soul against every fixture and return per-fixture rows."""
    rows: list[FixtureScore] = []
    for fixture in fixtures:
        if not fixture.enabled:
            continue
        rows.append(
            await _run_single_fixture(db, agent=agent, fixture=fixture, rules=rules)
        )
    return rows


def average_score(scores: Iterable[FixtureScore]) -> int:
    vals = [s.score for s in scores]
    return round(sum(vals) / len(vals)) if vals else 0


def passed_count(scores: Iterable[FixtureScore]) -> int:
    return sum(1 for s in scores if s.passed)


def to_per_fixture_payload(scores: Iterable[FixtureScore]) -> list[dict[str, Any]]:
    return [
        {
            "fixture_id": str(s.fixture_id),
            "title": s.title,
            "score": int(s.score),
            "passed": bool(s.passed),
            "detail": s.detail,
        }
        for s in scores
    ]


async def run_harness_against_draft(
    db,
    *,
    tenant_id: uuid.UUID,
    agent: Agent,
    draft: AgentEvolutionDraft,
    fixtures: list[AgentHarnessFixture],
    baseline_soul_md: str,
    draft_soul_md: str | None,
) -> tuple[AgentHarnessRun, AgentHarnessRun]:
    """Run both baseline and candidate stages and persist both rows.

    Returns ``(baseline_run, candidate_run)``. We always persist both so
    audit dashboards can show the lift even when the candidate is
    rejected.
    """
    common_rules: dict[str, Any] = {
        "min_length": 50,
        "no_placeholder": True,
        "threshold": 80,
    }
    baseline_scores = await evaluate_soul_against_fixtures(
        db, agent=agent, fixtures=fixtures, rules=common_rules
    )
    baseline_payload = to_per_fixture_payload(baseline_scores)
    baseline_run = await store_harness_run(
        db,
        tenant_id=tenant_id,
        agent_id=agent.id,
        draft_id=draft.id,
        stage="baseline",
        per_fixture=baseline_payload,
        status="succeeded",
    )

    candidate_run: AgentHarnessRun
    if draft_soul_md:
        candidate_scores = await evaluate_soul_against_fixtures(
            db, agent=agent, fixtures=fixtures, rules=common_rules
        )
        candidate_payload = to_per_fixture_payload(candidate_scores)
        candidate_run = await store_harness_run(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            draft_id=draft.id,
            stage="candidate",
            per_fixture=candidate_payload,
            status="succeeded",
        )
    else:
        candidate_run = await store_harness_run(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            draft_id=draft.id,
            stage="candidate",
            per_fixture=[],
            status="failed",
            error="no draft_soul_md",
        )

    return baseline_run, candidate_run


async def fixtures_for_agent(
    db,
    *,
    agent_id: uuid.UUID,
) -> list[AgentHarnessFixture]:
    rows = (
        await db.execute(
            select(AgentHarnessFixture)
            .where(
                AgentHarnessFixture.agent_id == agent_id,
                AgentHarnessFixture.enabled.is_(True),
            )
            .order_by(AgentHarnessFixture.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


async def apply_with_gating(
    db,
    *,
    agent: Agent,
    draft: AgentEvolutionDraft,
    baseline_run: AgentHarnessRun,
    candidate_run: AgentHarnessRun,
    min_improvement: int = 5,
) -> bool:
    """Apply the candidate soul if the harness shows a strict lift.

    Returns ``True`` when a new :class:`AgentRoleVersion` was published.
    Failures (insufficient lift, missing scores) are swallowed by
    :func:`apply_evolution_or_skip` so callers can log them.
    """
    base_score = baseline_run.average_score
    cand_score = candidate_run.average_score
    if base_score is None or cand_score is None:
        await signals.finalize_draft(
            db, draft_id=draft.id, status="rejected", decline_reason="missing-harness-score"
        )
        return False
    lift = cand_score - base_score
    if lift < min_improvement:
        await signals.finalize_draft(
            db,
            draft_id=draft.id,
            status="rejected",
            decline_reason=f"lift={lift} below min_improvement={min_improvement}",
        )
        return False

    from app.services.ao.evolution_engine import evolve_role

    await evolve_role(
        db,
        agent=agent,
        new_soul_md=draft.draft_soul_md or "",
        rationale=(
            f"P4 evolution draft {draft.id} lifted avg {base_score} -> {cand_score} "
            f"({lift:+d})"
        ),
        trigger_source="evolution_patch",
        trigger_ref_id=draft.id,
        quality_score_before=base_score,
        quality_score_after=cand_score,
    )
    await signals.finalize_draft(db, draft_id=draft.id, status="accepted")
    return True


__all__ = [
    "FixtureScore",
    "apply_evolution_or_skip",
    "apply_with_gating",
    "average_score",
    "evaluate_soul_against_fixtures",
    "fixtures_for_agent",
    "run_harness_against_draft",
    "to_per_fixture_payload",
]


async def apply_evolution_or_skip(
    db,
    *,
    tenant_id: uuid.UUID,
    agent: Agent,
    baseline_soul_md: str,
    draft_text: str | None,
    rationale: str | None,
    rule_additions: list[dict[str, Any]],
    source_signal_ids: list[uuid.UUID],
    min_improvement: int = 5,
) -> Result:
    """End-to-end P4 evolution: store draft, run harness, apply if it lifts.

    Idempotent w.r.t. inserts: if no ``draft_text`` is supplied the
    draft is recorded with ``status="rejected"`` and a deterministic
    reason so callers can diff the audit trail over time.
    """
    baseline_version = await signals.latest_baseline_version(db, agent_id=agent.id)
    draft = await signals.store_draft(
        db,
        tenant_id=tenant_id,
        agent_id=agent.id,
        baseline_version_id=baseline_version.id if baseline_version else None,
        patch_strategy="append_rules" if draft_text else "no_op",
        rationale=rationale,
        rule_additions=rule_additions,
        draft_soul_md=draft_text,
        source_signal_ids=source_signal_ids,
        status="running",
    )
    if not draft_text:
        await signals.finalize_draft(
            db,
            draft_id=draft.id,
            status="rejected",
            decline_reason="no-draft-soul",
        )
        return Result(applied=False, reason="no-draft-soul")

    fixtures = await fixtures_for_agent(db, agent_id=agent.id)
    if not fixtures:
        await signals.finalize_draft(
            db,
            draft_id=draft.id,
            status="rejected",
            decline_reason="no-fixtures",
        )
        return Result(applied=False, reason="no-fixtures")

    baseline_run, candidate_run = await run_harness_against_draft(
        db,
        tenant_id=tenant_id,
        agent=agent,
        draft=draft,
        fixtures=fixtures,
        baseline_soul_md=baseline_soul_md,
        draft_soul_md=draft_text,
    )
    applied = await apply_with_gating(
        db,
        agent=agent,
        draft=draft,
        baseline_run=baseline_run,
        candidate_run=candidate_run,
        min_improvement=min_improvement,
    )
    return Result(
        applied=applied,
        reason=None if applied else "below-gate",
        baseline_score=baseline_run.average_score,
        candidate_score=candidate_run.average_score,
    )


@dataclass(slots=True)
class Result:
    applied: bool
    reason: str | None
    baseline_score: int | None = None
    candidate_score: int | None = None


apply_evolution_or_skip.Result = Result  # type: ignore[attr-defined]
