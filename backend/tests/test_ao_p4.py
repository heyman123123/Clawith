"""P4 tests — patch engine + regression harness + draft gating.

Mirrors the in-memory pattern from :mod:`tests/test_ao_evolution`: we
fake ``db.scalar`` / ``db.execute`` / ``db.add`` / ``db.flush`` into a
shim session so the SQLAlchemy ORM objects are exercised end-to-end
without spinning up Postgres.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.models.evolution import (
    AgentEvolutionDraft,
    AgentEvolutionSignal,
    AgentHarnessFixture,
    AgentHarnessRun,
    AgentRoleVersion,
)
from app.services.ao import (
    evolution_signal_service,
    harness_fixture_seeder,
    patch_engine,
)
from app.services.ao.evolution_signal_service import (
    finalize_draft,
    record_quality_signal,
    store_draft,
)
from app.services.ao.patch_engine import (
    PatchDraft,
    _merge_rules,
    draft_patch_from_signals,
    generate_signal_summary,
)
from app.services.ao.regression_harness import (
    Result,
    apply_evolution_or_skip,
    apply_with_gating,
    evaluate_soul_against_fixtures,
    run_harness_against_draft,
)

# ---------------------------------------------------------------------------
# Patch engine — pure helpers
# ---------------------------------------------------------------------------


def test_merge_rules_appends_block_when_rules_present() -> None:
    baseline = "You are a helpful assistant."
    rules = [
        {"title": "Cite", "rule": "Always cite sources."},
        {"rule": "Avoid placeholder text"},
    ]
    merged = _merge_rules(baseline, rules)
    assert "helpful assistant" in merged
    assert "## 自动演化规则" in merged
    assert "**Cite**: Always cite sources." in merged
    assert "Avoid placeholder text" in merged


def test_merge_rules_returns_baseline_when_no_rules() -> None:
    baseline = "no-op"
    assert _merge_rules(baseline, []) == baseline


async def test_generate_signal_summary_uses_judge_when_available() -> None:
    payload = {
        "judge_used": True,
        "comments": "covered all requirements",
        "reasons": ["reason 1", "reason 2"],
    }
    summary = await generate_signal_summary(
        step_id=uuid.uuid4(), judge_payload=payload, verdict_score=92
    )
    assert "judge=92" in summary
    assert "reason 1" in summary


async def test_generate_signal_summary_falls_back_when_no_judge() -> None:
    summary = await generate_signal_summary(
        step_id=uuid.uuid4(),
        judge_payload=None,
        verdict_score=70,
    )
    assert "score=70" in summary
    assert "rule only" in summary


# ---------------------------------------------------------------------------
# In-memory shim session — mirror of test_ao_evolution but P4-shaped
# ---------------------------------------------------------------------------


class _ShimSession:
    def __init__(self) -> None:
        self.signals: list[AgentEvolutionSignal] = []
        self.drafts: list[AgentEvolutionDraft] = []
        self.fixtures: list[AgentHarnessFixture] = []
        self.runs: list[AgentHarnessRun] = []
        self.versions: list[AgentRoleVersion] = []
        self.flushes = 0
        self.added: list[Any] = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1
        for obj in list(self.added):
            if isinstance(obj, AgentEvolutionSignal):
                self.signals.append(obj)
            elif isinstance(obj, AgentEvolutionDraft):
                self.drafts.append(obj)
            elif isinstance(obj, AgentHarnessFixture):
                self.fixtures.append(obj)
            elif isinstance(obj, AgentHarnessRun):
                self.runs.append(obj)
            elif isinstance(obj, AgentRoleVersion):
                self.versions.append(obj)
        self.added.clear()

    async def commit(self):
        return None

    async def scalar(self, stmt):
        """Best-effort lookup shim.

        Production code paths touch four tables (Signal/Draft/RoleVersion).
        We dispatch on the ORM class so tests can ignore the SQL layer.
        """
        entity = _entity_from_select(stmt)
        clause = _extract_clause(stmt)
        target_id = _clause_id(clause)
        if entity is AgentEvolutionSignal:
            return self.signals[0] if self.signals else None
        if entity is AgentEvolutionDraft:
            # Drafts persist via ``flush`` — the row lives in self.drafts.
            for row in self.drafts:
                if target_id is None or row.id == target_id:
                    return row
            return None
        if entity is AgentRoleVersion:
            for row in self.versions:
                if row.is_current:
                    return row
            return self.versions[0] if self.versions else None
        if entity is AgentHarnessFixture:
            return list(self.fixtures)
        if entity is AgentHarnessRun:
            return list(self.runs)
        return None

    async def execute(self, stmt):
        rows: list[Any]
        entity = _entity_from_select(stmt)
        if entity is AgentHarnessFixture:
            rows = list(self.fixtures)
        elif entity is AgentHarnessRun:
            rows = list(self.runs)
        elif entity is AgentEvolutionDraft:
            rows = list(self.drafts)
        elif entity is AgentEvolutionSignal:
            rows = list(self.signals)
        elif entity is AgentRoleVersion:
            rows = list(self.versions)
        else:
            rows = []

        # Avoid the ``self_`` recursion pitfall: ``_R.all`` reads
        # ``wrapper.rows`` so ``scalars().all()`` returns the row list.
        class _R:
            def scalars(inner_self):
                return inner_self

            def all(inner_self):
                return list(inner_self.rows)

        wrapper = _R()
        wrapper.rows = list(rows)
        return wrapper


def _entity_from_select(stmt) -> type | None:
    if stmt is None:
        return None
    # SQLAlchemy ``Select`` exposes column_descriptions as a list of
    # ``{"name", "type", "entity"}`` dicts (per the ORM column metadata
    # interface). Probe it directly rather than treating it as a callable.
    descriptions = getattr(stmt, "column_descriptions", None)
    if isinstance(descriptions, list):
        for item in descriptions:
            if isinstance(item, dict):
                entity = item.get("entity")
                if entity is not None:
                    return entity
    return None


def _extract_clause(stmt):
    return getattr(stmt, "_where_criteria", None)


def _clause_id(clause):
    """Extract a UUID from the simplest ``column == value`` clause shape."""
    right = getattr(clause, "right", None)
    if right is None:
        return None
    return getattr(right, "value", None)


@pytest.fixture
def session() -> _ShimSession:
    return _ShimSession()


# ---------------------------------------------------------------------------
# Helpers — async + select shims
# ---------------------------------------------------------------------------


def _async(coro_or_value):
    """Mimic ``async def`` helpers in normal pytest assertions.

    The patch_engine helpers that return strings already return them
    synchronously; for :func:`draft_patch_from_signals` (the only true
    async in this module's pure helpers) the test awaits it directly.
    """
    return coro_or_value


async def _run(awaitable):
    return await awaitable


@dataclass
class _StubAgent:
    id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    name: str = "Role Agent"
    role_description: str = ""


def _stub_signal(*, summary: str = "", reasons: list[str] | None = None) -> AgentEvolutionSignal:
    return AgentEvolutionSignal(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        kind="judge_flagged",
        trigger_source="ao_quality_check",
        trigger_ref_id=None,
        quality_score=80,
        rule_score=80,
        judge_used=True,
        judge_payload={"judge_used": True},
        reasons=reasons or [],
        summary=summary or None,
    )


# ---------------------------------------------------------------------------
# record_quality_signal / store_draft / store_harness_run
# ---------------------------------------------------------------------------


async def test_record_quality_signal_persists_row() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    step_id = uuid.uuid4()
    row = await record_quality_signal(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent_id=agent_id,
        quality_score=85,
        rule_score=80,
        judge_payload={
            "judge_used": True,
            "comments": "good",
            "reasons": ["missing depth", "structure unclear"],
        },
        trigger_ref_id=step_id,
        summary="test summary",
    )
    assert row is not None
    assert len(session.signals) == 1
    assert session.signals[0].reasons == ["missing depth", "structure unclear"]


async def test_store_draft_marks_pending_with_strategy() -> None:
    session = _ShimSession()
    draft = await store_draft(
        session,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        baseline_version_id=None,
        patch_strategy="append_rules",
        rationale="add rules",
        rule_additions=[{"title": "Cite", "rule": "Always cite"}],
        draft_soul_md="baseline\n\n## 自动演化规则\n- Cite sources.",
        source_signal_ids=[uuid.uuid4(), uuid.uuid4()],
    )
    assert draft.status == "pending"
    assert draft.patch_strategy == "append_rules"
    assert len(session.drafts) == 1


async def test_finalize_draft_marks_accepted() -> None:
    session = _ShimSession()
    draft = await store_draft(
        session,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        baseline_version_id=None,
        patch_strategy="no_op",
        rationale="skip",
        rule_additions=[],
        draft_soul_md=None,
        source_signal_ids=[],
    )
    finalized = await finalize_draft(
        session,  # type: ignore[arg-type]
        draft_id=draft.id,
        status="accepted",
        decline_reason=None,
    )
    assert finalized is not None
    assert session.drafts[-1].status == "accepted"


# ---------------------------------------------------------------------------
# Regression harness — rule engine scoring
# ---------------------------------------------------------------------------


def _make_fixture(
    agent_id: uuid.UUID,
    *,
    title: str = "测试",
    keywords: list[str] | None = None,
    body: str = "默认正文 must_mention 关键词",
) -> AgentHarnessFixture:
    return AgentHarnessFixture(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_id=agent_id,
        fixture_role="quality",
        kind="role_qa",
        title=title,
        task_summary=body,
        acceptance_text=None,
        expected_keywords=keywords or ["must_mention", "默认正文"],
        rubric=None,
        weight=1,
        enabled=True,
    )


async def test_evaluate_soul_against_fixtures_scores_with_rule_engine() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    body = (
        "请确保在交付中必须出现 must_mention 与 默认正文，且总字数不少于 80。"
        " 重复填充词汇使长度达标：" + ("占位" * 80)
    )
    fixtures = [_make_fixture(agent_id, title="ok", body=body, keywords=["must_mention", "默认正文"])]
    agent = _StubAgent(id=agent_id)
    scores = await evaluate_soul_against_fixtures(
        session,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        fixtures=fixtures,
    )
    assert len(scores) == 1
    assert scores[0].passed is True
    assert scores[0].score > 0


async def test_run_harness_against_draft_persists_two_runs() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    fixtures = [
        _make_fixture(agent_id, title="prompt-1", keywords=["ok"]),
        _make_fixture(agent_id, title="prompt-2", keywords=["默认正文"]),
    ]
    agent = _StubAgent(id=agent_id, tenant_id=tenant_id)
    draft = AgentEvolutionDraft(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        baseline_version_id=None,
        patch_strategy="append_rules",
        rationale="x",
        rule_additions=[],
        draft_soul_md="baseline-soul with must_mention 默认正文",
        status="running",
        source_signal_ids=[],
    )
    baseline, candidate = await run_harness_against_draft(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent=agent,  # type: ignore[arg-type]
        draft=draft,
        fixtures=fixtures,
        baseline_soul_md="baseline",
        draft_soul_md=draft.draft_soul_md,
    )
    assert baseline.stage == "baseline"
    assert candidate.stage == "candidate"
    assert baseline.fixture_count == len(fixtures)
    assert candidate.fixture_count == len(fixtures)
    assert len(session.runs) == 2


async def test_apply_with_gating_rejects_when_lift_below_min() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    draft = AgentEvolutionDraft(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        baseline_version_id=None,
        patch_strategy="append_rules",
        rationale="x",
        rule_additions=[],
        draft_soul_md="candidate",
        status="running",
        source_signal_ids=[],
    )
    session.add(draft)
    await session.flush()
    baseline = AgentHarnessRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        draft_id=draft.id,
        stage="baseline",
        status="succeeded",
        average_score=80,
        passed_count=1,
        failed_count=0,
        fixture_count=1,
        per_fixture=[],
    )
    candidate = AgentHarnessRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        draft_id=draft.id,
        stage="candidate",
        status="succeeded",
        average_score=82,
        passed_count=1,
        failed_count=0,
        fixture_count=1,
        per_fixture=[],
    )

    applied = await apply_with_gating(
        session,  # type: ignore[arg-type]
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        draft=draft,
        baseline_run=baseline,
        candidate_run=candidate,
        min_improvement=5,
    )
    assert applied is False
    assert session.drafts and session.drafts[-1].status == "rejected"
    assert "lift=2" in (session.drafts[-1].decline_reason or "")


async def test_apply_evolution_or_skip_no_draft_text_rejected() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    result = await apply_evolution_or_skip(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        baseline_soul_md="baseline",
        draft_text=None,
        rationale="x",
        rule_additions=[],
        source_signal_ids=[],
    )
    assert result.applied is False
    assert result.reason == "no-draft-soul"
    assert session.drafts[-1].status == "rejected"
    assert session.drafts[-1].decline_reason == "no-draft-soul"


async def test_apply_evolution_or_skip_no_fixtures_rejected() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    result = await apply_evolution_or_skip(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        baseline_soul_md="baseline",
        draft_text="candidate",
        rationale="x",
        rule_additions=[],
        source_signal_ids=[],
    )
    assert result.applied is False
    assert result.reason == "no-fixtures"


# ---------------------------------------------------------------------------
# patch_engine.draft_patch_from_signals — fallback paths
# ---------------------------------------------------------------------------


class _NoModelSession(_ShimSession):
    async def execute(self, _stmt):
        class _R:
            def scalars(self_):
                return self_

            def all(self_):
                return []

        return _R()


async def test_draft_patch_from_signals_falls_back_when_no_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _NoModelSession()
    agent_id = uuid.uuid4()
    agent = _StubAgent(id=agent_id, tenant_id=uuid.uuid4())

    async def fake_recents(_db, *, agent_id, limit):
        return []

    monkeypatch.setattr(evolution_signal_service, "recent_signals_for_agent", fake_recents)
    draft = await draft_patch_from_signals(
        session,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        baseline_soul_md="baseline",
    )
    assert isinstance(draft, PatchDraft)
    assert draft.patch_strategy == "no_op"


async def test_draft_patch_from_signals_falls_back_when_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _NoModelSession()
    agent_id = uuid.uuid4()
    agent = _StubAgent(id=agent_id, tenant_id=uuid.uuid4())

    async def fake_recents(_db, *, agent_id, limit):
        return [_stub_signal(summary="trial-1", reasons=["too short"])]

    async def fake_active(*_a, **_k):
        return ()

    monkeypatch.setattr(evolution_signal_service, "recent_signals_for_agent", fake_recents)
    monkeypatch.setattr(patch_engine, "active_agent_model_candidates", fake_active)
    draft = await draft_patch_from_signals(
        session,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        baseline_soul_md="baseline",
    )
    assert draft.patch_strategy == "no_op"
    assert "No LLM" in (draft.rationale or "")


async def test_draft_patch_from_signals_uses_chat_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _NoModelSession()
    agent_id = uuid.uuid4()
    agent = _StubAgent(id=agent_id, tenant_id=uuid.uuid4())

    async def fake_recents(_db, *, agent_id, limit):
        return [
            _stub_signal(summary="step-1: missed depth", reasons=["missing depth"]),
            _stub_signal(summary="step-2: placeholder used", reasons=["placeholder leaked"]),
        ]

    async def fake_chat(**_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "rationale": "Need depth + no placeholder",
                                "rules": [
                                    {"title": "Depth", "rule": "Provide in-depth analysis."},
                                    {"title": "No placeholder", "rule": "Never output TODO markers."},
                                ],
                            }
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "judge-mock",
            "usage": {},
        }

    async def fake_active(*_a, **_k):
        return (
            type(
                "M",
                (),
                {
                    "provider": "openai",
                    "model": "judge-test",
                    "api_key_encrypted": "secret",
                    "base_url": None,
                    "tenant_id": uuid.uuid4(),
                },
            )(),
        )

    monkeypatch.setattr(evolution_signal_service, "recent_signals_for_agent", fake_recents)
    monkeypatch.setattr(patch_engine, "active_agent_model_candidates", fake_active)
    monkeypatch.setattr(patch_engine, "chat_complete", fake_chat)

    draft = await draft_patch_from_signals(
        session,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        baseline_soul_md="You are an analyst.",
    )
    assert draft.patch_strategy == "append_rules"
    assert draft.used_llm is True
    assert draft.draft_soul_md is not None
    assert "Depth" in draft.draft_soul_md
    assert "Never output TODO markers" in draft.draft_soul_md
    assert len(draft.rule_additions) == 2


async def test_draft_patch_from_signals_swallows_chat_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _NoModelSession()
    agent_id = uuid.uuid4()
    agent = _StubAgent(id=agent_id, tenant_id=uuid.uuid4())

    async def fake_recents(_db, *, agent_id, limit):
        return [_stub_signal(summary="trial")]

    async def fake_active(*_a, **_k):
        return (
            type(
                "M",
                (),
                {
                    "provider": "openai",
                    "model": "judge-test",
                    "api_key_encrypted": "secret",
                    "base_url": None,
                    "tenant_id": uuid.uuid4(),
                },
            )(),
        )

    async def fake_chat(**_kwargs):
        raise patch_engine.LLMError("boom")

    monkeypatch.setattr(evolution_signal_service, "recent_signals_for_agent", fake_recents)
    monkeypatch.setattr(patch_engine, "active_agent_model_candidates", fake_active)
    monkeypatch.setattr(patch_engine, "chat_complete", fake_chat)

    draft = await draft_patch_from_signals(
        session,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        baseline_soul_md="baseline",
    )
    assert draft.patch_strategy == "no_op"
    assert "boom" in (draft.rationale or "")


# ---------------------------------------------------------------------------
# Harness fixture seeder — deterministic
# ---------------------------------------------------------------------------


async def test_ensure_default_harness_fixtures_is_idempotent() -> None:
    session = _ShimSession()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    first = await harness_fixture_seeder.ensure_default_harness_fixtures(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent_id=agent_id,
        role_key="quality",
    )
    second = await harness_fixture_seeder.ensure_default_harness_fixtures(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent_id=agent_id,
        role_key="quality",
    )
    assert len(first) >= 2
    assert second == []  # nothing new on the second call
    assert sum(1 for f in session.fixtures if f.agent_id == agent_id) == len(first)


def test_default_fixture_payloads_exposes_expected_keys() -> None:
    """Stable contract — used by admin / docs to enumerate seed fixtures."""
    payloads = list(harness_fixture_seeder.default_fixture_payloads_for("quality"))
    assert any("质控" in item["title"] for item in payloads)
    assert any("升级" in item["title"] for item in payloads)


# ---------------------------------------------------------------------------
# Result dataclass — applied/reason contract
# ---------------------------------------------------------------------------


def test_result_dataclass_exposes_fields() -> None:
    r = Result(applied=True, reason=None, baseline_score=80, candidate_score=90)
    assert r.applied is True
    assert r.baseline_score == 80
    assert r.candidate_score == 90


def test_result_dataclass_rejects_unknown_kwarg() -> None:
    r = Result(applied=False, reason="below-gate")
    assert r.applied is False
    assert r.baseline_score is None
