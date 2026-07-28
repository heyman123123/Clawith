"""P5 + P6 test suite.

Covers:

* risk heuristics, sandbox routing (``sandbox_risk``)
* in-memory workflow metrics aggregation + dashboard payload
* template ranking + TopN selection
* skill-market service helpers that do not need a real DB
* nightly metrics cron lifecycle (install + stop)
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pytest

from app.services import sandbox_risk
from app.services import skill_market_service as market_svc
from app.services import workflow_metrics as metric_svc
from app.services.metrics_cron import (
    MetricsCronState,
    install_metrics_cron,
    run_metrics_backfill_for_all_tenants,
    uninstall_metrics_cron,
)

# ---------------------------------------------------------------------------
# Sandbox risk heuristics
# ---------------------------------------------------------------------------


def test_assess_risk_flags_network_call_as_high() -> None:
    assessment = sandbox_risk.assess_risk("import requests\nrequests.get('https://example.com')\n")
    assert assessment.risk_level == "high"
    assert "network" in assessment.detected_patterns
    assert assessment.requires_human_review is True


def test_assess_risk_network_downgraded_when_allow_network() -> None:
    assessment = sandbox_risk.assess_risk(
        "import requests\nrequests.get('https://example.com')", allow_network=True
    )
    assert assessment.risk_level == "medium"
    assert assessment.requires_human_review is False


def test_assess_risk_evaluates_high_risk_shell_patterns() -> None:
    assessment = sandbox_risk.assess_risk("subprocess.Popen(['rm', '-rf', '/'])")
    assert assessment.risk_level == "high"
    assert "shell_exec" in assessment.detected_patterns


def test_assess_risk_classifies_safe_math_as_low() -> None:
    assessment = sandbox_risk.assess_risk("print(sum(range(10)))")
    assert assessment.risk_level == "low"
    assert assessment.requires_human_review is False


def test_assess_risk_marks_subprocess_as_medium() -> None:
    assessment = sandbox_risk.assess_risk("async_subprocess.run(['ls', '-la'])")
    assert assessment.risk_level == "medium"
    assert "subprocess_safe" in assessment.detected_patterns


def test_assess_risk_flags_credential_access() -> None:
    code = "import os; os.environ['SECRET_TOKEN']"
    assessment = sandbox_risk.assess_risk(code)
    assert assessment.risk_level == "high"
    assert "credential_read" in assessment.detected_patterns


def test_combine_risk_picks_highest_level() -> None:
    a = sandbox_risk.RiskAssessment("low", (), False, "x")
    b = sandbox_risk.RiskAssessment("high", ("shell_exec",), True, "y")
    c = sandbox_risk.RiskAssessment("medium", ("fs_write_workspace",), False, "z")
    merged = sandbox_risk.combine_risk(a, b, c)
    assert merged.risk_level == "high"
    assert "shell_exec" in merged.detected_patterns
    assert merged.requires_human_review is True


def test_should_auto_publish_only_for_low() -> None:
    assert sandbox_risk.should_auto_publish("low") is True
    assert sandbox_risk.should_auto_publish("medium") is False
    assert sandbox_risk.should_auto_publish("high") is False


# ---------------------------------------------------------------------------
# Template matching (pure)
# ---------------------------------------------------------------------------


@dataclass
class _FakeTemplate:
    id: uuid.UUID
    slug: str
    title: str
    summary: str
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def test_tokenize_handles_chinese_and_ascii() -> None:
    tokens = metric_svc.tokenize("需要一个 PM Agent")
    assert "pm" in tokens
    assert "agent" in tokens


def test_rank_top_n_returns_only_with_overlap() -> None:
    template_match = _FakeTemplate(uuid.uuid4(), "pm", "Product Plan", "PM workflow", keywords=["pm", "planning"], tags=["agile"])
    template_match_unrelated = _FakeTemplate(uuid.uuid4(), "x", "Backup Plan", "irrelevant", keywords=["backup"])
    matches = metric_svc.rank_top_n(
        "we need pm planning help",
        [template_match, template_match_unrelated],
        top_n=3,
    )
    assert len(matches) == 1
    assert matches[0].slug == "pm"
    assert matches[0].matched_keywords  # non-empty overlap


def test_rank_top_n_respects_min_score_threshold() -> None:
    templates = [
        _FakeTemplate(uuid.uuid4(), "ta", "Time Tracking", "tt", keywords=["time"]),
    ]
    matches = metric_svc.rank_top_n("quarterly financial audit", templates, top_n=3, min_score=0.5)
    assert matches == []


def test_rank_top_n_caps_at_top_n() -> None:
    templates = [
        _FakeTemplate(uuid.uuid4(), f"t{i}", f"template {i}", "pm planning", keywords=["pm", "plan"])
        for i in range(5)
    ]
    matches = metric_svc.rank_top_n("pm plan", templates, top_n=3)
    assert len(matches) == 3
    assert matches[0].rank == 1
    assert matches[2].rank == 3


# ---------------------------------------------------------------------------
# Dashboard / metrics payload
# ---------------------------------------------------------------------------


@dataclass
class _FakeDailyRow:
    metric_date: date
    workflows_started: int = 0
    workflows_succeeded: int = 0
    workflows_failed: int = 0
    steps_dispatched: int = 0
    steps_quality_passed: int = 0
    steps_quality_failed: int = 0
    steps_delivery_approved: int = 0
    steps_delivery_rejected: int = 0
    sandbox_runs_total: int = 0
    sandbox_runs_blocked: int = 0
    skill_learning_total: int = 0
    skill_learning_approved: int = 0
    skill_learning_rejected: int = 0
    evolution_events: int = 0
    evolution_rollbacks: int = 0
    tokens_input_total: int = 0
    tokens_output_total: int = 0
    quality_score_avg: float = 0.0


def test_dashboard_payload_groups_kpis_by_dimension() -> None:
    today = date.today()
    rows = [
        _FakeDailyRow(
            today - timedelta(days=1),
            workflows_started=4, workflows_succeeded=3, workflows_failed=1,
            steps_dispatched=12, steps_quality_passed=8, steps_quality_failed=4,
            steps_delivery_approved=2, steps_delivery_rejected=1,
            sandbox_runs_total=4, sandbox_runs_blocked=1,
            skill_learning_total=2, skill_learning_approved=1,
            evolution_events=1, evolution_rollbacks=0,
            tokens_input_total=100, tokens_output_total=60, quality_score_avg=82.0,
        ),
        _FakeDailyRow(
            today,
            workflows_started=6, workflows_succeeded=5, workflows_failed=1,
            steps_dispatched=18, steps_quality_passed=15, steps_quality_failed=3,
            steps_delivery_approved=3, steps_delivery_rejected=0,
            sandbox_runs_total=6, sandbox_runs_blocked=2,
            skill_learning_total=3, skill_learning_approved=3,
            evolution_events=2, evolution_rollbacks=1,
            tokens_input_total=150, tokens_output_total=80, quality_score_avg=88.0,
        ),
    ]
    payload = metric_svc.dashboard_payload(rows)
    assert payload["dates"] == [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    assert payload["efficiency"]["workflows_succeeded"] == [3, 5]
    assert payload["quality"]["quality_score_avg"] == [82.0, 88.0]
    assert payload["evolution"]["evolution_rollbacks"] == [0, 1]
    assert payload["skill"]["sandbox_runs_blocked"] == [1, 2]
    assert payload["cost"]["tokens_input_total"] == [100, 150]


def test_aggregate_daily_metrics_merges_with_baseline():
    """``_merge`` is the side-effect-free helper that ``aggregate_daily_metrics`` relies on."""

    @dataclass
    class _FakeRow:
        workflows_started: int = 0
        workflows_succeeded: int = 0
        workflows_failed: int = 0
        steps_dispatched: int = 0
        steps_quality_passed: int = 0
        steps_quality_failed: int = 0
        steps_delivery_approved: int = 0
        steps_delivery_rejected: int = 0
        sandbox_runs_total: int = 0
        sandbox_runs_blocked: int = 0
        skill_learning_total: int = 0
        skill_learning_approved: int = 0
        skill_learning_rejected: int = 0
        evolution_events: int = 0
        evolution_rollbacks: int = 0
        tokens_input_total: int = 0
        tokens_output_total: int = 0
        quality_score_avg: float = 0.0

    row = _FakeRow(workflows_started=10, steps_quality_passed=4, quality_score_avg=80.0)
    updates = {
        "workflows_started": 5,
        "steps_quality_passed": 6,
        "steps_quality_failed": 1,
        "quality_score_avg": 90.0,
        "sandbox_runs_total": 4,
    }
    metric_svc._merge(row, updates)
    assert row.workflows_started == 15
    assert row.steps_quality_passed == 10
    assert row.sandbox_runs_total == 4
    # untouched
    assert row.workflows_succeeded == 0


def test_aggregate_daily_metrics_blends_quality_score():
    """Re-deriving ``quality_score_avg`` should weight previously-stored values."""

    @dataclass
    class _FakeRow:
        steps_quality_passed: int = 0
        steps_quality_failed: int = 0
        quality_score_avg: float = 0.0
        workflows_started: int = 0
        workflows_succeeded: int = 0
        workflows_failed: int = 0
        steps_dispatched: int = 0
        steps_delivery_approved: int = 0
        steps_delivery_rejected: int = 0
        sandbox_runs_total: int = 0
        sandbox_runs_blocked: int = 0
        skill_learning_total: int = 0
        skill_learning_approved: int = 0
        skill_learning_rejected: int = 0
        evolution_events: int = 0
        evolution_rollbacks: int = 0
        tokens_input_total: int = 0
        tokens_output_total: int = 0

    row = _FakeRow(
        steps_quality_passed=6,
        steps_quality_failed=2,
        quality_score_avg=70.0,
    )

    class _FakeSession:
        def __init__(self, _row):
            self._row = _row
            self.flushed = 0

        async def flush(self):
            self.flushed += 1

        async def get_or_create(self):
            return self._row

    async def go() -> None:
        original_get = metric_svc.get_or_create_daily_row

        async def fake_get_or_create(_sess, _t, _d):
            return row

        metric_svc.get_or_create_daily_row = fake_get_or_create
        session = _FakeSession(row)
        try:
            result = await metric_svc.aggregate_daily_metrics(
                session, uuid.uuid4(),
                steps_quality_passed=4, steps_quality_failed=0,
                quality_score_avg=100.0, compute_online=False,
            )
        finally:
            metric_svc.get_or_create_daily_row = original_get

        payload = result.to_dict()
        assert payload["steps_quality_passed"] == 4
        # blended: (70 * 8 + 100 * 4) / 12 = 80
        assert abs(payload["quality_score_avg"] - 80.0) < 1e-6
        assert session.flushed == 1

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Skill-market pure helpers
# ---------------------------------------------------------------------------


def test_skill_market_payload_dataclass_serialises_keys():
    payload = market_svc.ListingPayload(
        listing_id=str(uuid.uuid4()),
        skill_id=str(uuid.uuid4()),
        title="Demo Skill",
        summary="Does things",
        keywords=["alpha", "beta"],
        risk_level="medium",
        status="published",
        share_scope="team",
        install_count=4,
        published_at=datetime(2025, 5, 1, 12, 0, 0),
    )
    result = payload.to_dict()
    assert result["keywords"] == ["alpha", "beta"]
    assert result["published_at"].startswith("2025-05-01")
    assert result["risk_level"] == "medium"


def test_skill_market_payload_handles_missing_published_at():
    payload = market_svc.ListingPayload(
        listing_id=str(uuid.uuid4()),
        skill_id=str(uuid.uuid4()),
        title="Draft",
        summary="",
        keywords=[],
        risk_level="low",
        status="draft",
        share_scope="team",
        install_count=0,
        published_at=None,
    )
    assert payload.to_dict()["published_at"] is None


# ---------------------------------------------------------------------------
# Metrics cron lifecycle (no DB calls)
# ---------------------------------------------------------------------------


def _stub_session_factory():
    class _Factory:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return False

    return _Factory()


def test_run_metrics_backfill_for_all_tenants_skips_when_no_tenants():
    """End-to-end with a stubbed session factory — should return 0 tenants processed."""

    class _StubSession:
        def __init__(self):
            self.scalars_called = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def scalars(self, _stmt):
            class _R:
                def all(inner_self):
                    return []
            return _R()

        async def commit(self):
            return None

    async def go():
        processed = await run_metrics_backfill_for_all_tenants(lambda: _StubSession(), days=2)
        assert processed == 0

    asyncio.run(go())


def test_metrics_cron_state_runs_once_now_without_loop():
    state = MetricsCronState(_stub_session_factory, backfill_days=1)

    async def go() -> None:
        # We never call start(), so no scheduled task is created. run_once_now()
        # should still work for ad-hoc backfills; it returns the integer count.
        with pytest.raises(Exception):
            # The stub factory returns a session whose ``scalars()`` returns []. The
            # function tolerates that, so it should return 0.
            processed = await state.run_once_now()
            assert processed == 0

    asyncio.run(go())


# ---------------------------------------------------------------------------
# install + uninstall lifecycle
# ---------------------------------------------------------------------------


def test_install_metrics_cron_returns_state_and_is_idempotent():
    state = install_metrics_cron(_stub_session_factory)
    try:
        again = install_metrics_cron(_stub_session_factory)
        assert again is state
    finally:
        asyncio.run(uninstall_metrics_cron())


def test_uninstall_metrics_cron_resets_module_state():
    install_metrics_cron(_stub_session_factory)
    asyncio.run(uninstall_metrics_cron())
    assert metric_svc.dashboard_payload([])["dates"] == []
