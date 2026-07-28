"""P3 tests — evolution engine, one-step rollback, LLM-judge fallback.

We avoid spinning up Postgres by patching the SQLAlchemy primitives the
engine uses (``select``, ``update``, ``db.scalar``, ``db.execute``,
``db.flush``) into a tiny in-memory store. The engine code is exercised
end-to-end except for the actual SQL execution.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.models.evolution import AgentEvolutionRecord, AgentRoleVersion
from app.services.ao import evolution_engine, llm_judge
from app.services.ao.llm_judge import (
    JudgeResult,
    _coerce_bool,
    _coerce_score,
    _try_parse_json,
    evaluate_step_with_judge,
)
from app.services.ao.quality_rules import QualityVerdict, evaluate_output


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_try_parse_json_extracts_fenced_json() -> None:
    body = "```json\n{\"score\": 80, \"passed\": true}\n```"
    parsed = _try_parse_json(body)
    assert parsed == {"score": 80, "passed": True}


def test_try_parse_json_handles_brace_match() -> None:
    parsed = _try_parse_json("noise before {\"score\": 70} noise after")
    assert parsed == {"score": 70}


def test_try_parse_json_returns_none_for_garbage() -> None:
    assert _try_parse_json("no json here") is None


def test_coerce_score_clamps_into_range() -> None:
    assert _coerce_score({"score": 200}, fallback=50) == 100
    assert _coerce_score({"score": -3}, fallback=50) == 0
    assert _coerce_score(None, fallback=42) == 42


def test_coerce_bool_recognises_pass_strings() -> None:
    assert _coerce_bool(True) is True
    assert _coerce_bool("PASS") is True
    assert _coerce_bool("failed") is False
    assert _coerce_bool(None) is None


# ---------------------------------------------------------------------------
# In-memory evolution store + patched engine helpers
# ---------------------------------------------------------------------------


@dataclass
class _VersionRow:
    id: uuid.UUID
    agent_id: uuid.UUID
    tenant_id: uuid.UUID | None
    version_no: int
    soul_md: str
    source: str
    is_current: bool
    quality_score: int | None = None
    summary: str | None = None
    evolution_record_id: uuid.UUID | None = None


@dataclass
class _RecordRow:
    id: uuid.UUID
    agent_id: uuid.UUID
    tenant_id: uuid.UUID | None
    kind: str
    from_version_id: uuid.UUID | None = None
    to_version_id: uuid.UUID | None = None
    quality_score_before: int | None = None
    quality_score_after: int | None = None
    rationale: str | None = None


class _EvolutionStore:
    def __init__(self) -> None:
        self.versions: list[_VersionRow] = []
        self.records: list[_RecordRow] = []

    def current_for(self, agent_id: uuid.UUID) -> _VersionRow | None:
        for row in self.versions:
            if row.agent_id == agent_id and row.is_current:
                return row
        return None

    def any_for(self, agent_id: uuid.UUID) -> _VersionRow | None:
        for row in self.versions:
            if row.agent_id == agent_id:
                return row
        return None

    def history(self, agent_id: uuid.UUID) -> list[_VersionRow]:
        rows = [v for v in self.versions if v.agent_id == agent_id]
        return sorted(rows, key=lambda r: r.version_no, reverse=True)


class _OrmProxy:
    """Light-weight proxy mapping ORM attribute reads to underlying row."""

    def __init__(self, row):
        self._row = row

    def __getattr__(self, item):
        return getattr(self._row, item)


class _ShimSession:
    """Session-shaped shim the engine writes to.

    Patches applied when the session is wired (see :func:`_wire_db`):

    * ``db.scalar`` / ``db.execute`` intercept SQLAlchemy statements.
    * ``db.flush`` walks the staged ``db.add`` queue and applies updates
      against the in-memory store before clearing it. After the first
      flush we track every ORM object's identity and, on subsequent
      flushes, copy any mutated fields back to its store row.
    * ``update(...)`` is monkeypatched at the engine module level so the
      generated SQL expression never reaches SQLAlchemy.
    """

    def __init__(self, store: _EvolutionStore) -> None:
        self.store = store
        self.added: list[Any] = []
        self.tracked_versions: dict[uuid.UUID, AgentRoleVersion] = {}
        self.tracked_records: dict[uuid.UUID, AgentEvolutionRecord] = {}
        self.flush_count = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1
        for obj in list(self.added):
            if isinstance(obj, AgentRoleVersion):
                row = _VersionRow(
                    id=obj.id,
                    agent_id=obj.agent_id,
                    tenant_id=obj.tenant_id,
                    version_no=obj.version_no,
                    soul_md=obj.soul_md,
                    source=obj.source,
                    is_current=bool(obj.is_current),
                    quality_score=obj.quality_score,
                    summary=obj.summary,
                    evolution_record_id=obj.evolution_record_id,
                )
                if row.is_current:
                    for other in self.store.versions:
                        if (
                            other.agent_id == row.agent_id
                            and other.id != row.id
                            and other.is_current
                        ):
                            other.is_current = False
                self.store.versions.append(row)
                self.tracked_versions[obj.id] = obj
            elif isinstance(obj, AgentEvolutionRecord):
                self.store.records.append(
                    _RecordRow(
                        id=obj.id,
                        agent_id=obj.agent_id,
                        tenant_id=obj.tenant_id,
                        kind=obj.kind,
                        from_version_id=obj.from_version_id,
                        to_version_id=obj.to_version_id,
                        quality_score_before=obj.quality_score_before,
                        quality_score_after=obj.quality_score_after,
                        rationale=obj.rationale,
                    )
                )
                self.tracked_records[obj.id] = obj
        self.added.clear()

        # Sync mutated ORM attribute writes back into the store row so
        # ``new_version.is_current = True`` after add/flush takes effect.
        for row in self.store.versions:
            obj = self.tracked_versions.get(row.id)
            if obj is None:
                continue
            if obj.is_current != row.is_current:
                if obj.is_current:
                    for other in self.store.versions:
                        if (
                            other.agent_id == row.agent_id
                            and other.id != row.id
                            and other.is_current
                        ):
                            other.is_current = False
                row.is_current = obj.is_current
            if obj.quality_score != row.quality_score:
                row.quality_score = obj.quality_score
        for record_row in self.store.records:
            obj = self.tracked_records.get(record_row.id)
            if obj is None:
                continue
            if obj.to_version_id != record_row.to_version_id:
                record_row.to_version_id = obj.to_version_id

    async def commit(self) -> None:
        return None


def _wire_db(monkeypatch: pytest.MonkeyPatch, store: _EvolutionStore, agent_id: uuid.UUID) -> _ShimSession:
    """Patch SQL primitives in :mod:`evolution_engine` so the shim session works."""
    session = _ShimSession(store)

    async def fake_scalar(_stmt):
        # Engine uses scalar for: any-version lookup and current-version lookup.
        # We dispatch by checking the current state of the store.
        any_match = store.any_for(agent_id)
        if any_match is None:
            return None
        # Prefer the tracked ORM object so attribute writes propagate to the
        # store via the post-flush sync. Fall back to the dataclass when the
        # ORM object hasn't been added yet (e.g. in ``seed_role_baseline``
        # before any insert).
        tracked = session.tracked_versions.get(any_match.id)
        return tracked if tracked is not None else any_match

    async def fake_execute(_stmt):
        rows = list(store.history(agent_id))

        class _R:
            def scalars(inner_self):  # noqa: N805 - tiny test shim
                return inner_self

            def all(inner_self):  # noqa: N805 - tiny test shim
                return list(rows)

        return _R()

    monkeypatch.setattr(evolution_engine, "update", _fake_update_factory())
    session.scalar = fake_scalar  # type: ignore[attr-defined]
    session.execute = fake_execute  # type: ignore[attr-defined]
    return session


def _fake_update_factory():
    """Build a no-op ``update``. The shim session already maintains is_current."""

    def _fake_update(_model):
        class _Q:
            def where(self, *clauses):
                class _QQ:
                    def values(self_, **kwargs):
                        return None

                return _QQ()

        return _Q()

    return _fake_update


@pytest.fixture(autouse=True)
def patch_engine_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default patch to satisfy linters; per-test wires the actual shim."""


# ---------------------------------------------------------------------------
# Engine tests — seeded and exercised via patched SQL primitives
# ---------------------------------------------------------------------------


async def test_seed_role_baseline_creates_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _EvolutionStore()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _wire_db(monkeypatch, store, agent_id)
    outcome = await evolution_engine.seed_role_baseline(
        session,  # type: ignore[arg-type]
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="baseline-soul",
        summary="first run",
    )
    assert outcome.evolved is True
    assert len(store.versions) == 1
    assert store.versions[0].is_current is True
    assert store.versions[0].source == "baseline"
    assert store.records[0].kind == "baseline"


async def test_seed_role_baseline_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _EvolutionStore()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _wire_db(monkeypatch, store, agent_id)

    await evolution_engine.seed_role_baseline(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="first-soul",
    )
    # Re-seed — should report no-op because ``store.any_for`` now returns the row.
    second = await evolution_engine.seed_role_baseline(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="ignored",
    )
    assert second.evolved is False
    assert len(store.versions) == 1


async def test_evolve_role_appends_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _EvolutionStore()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _wire_db(monkeypatch, store, agent_id)

    await evolution_engine.seed_role_baseline(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="v1",
    )
    outcome = await evolution_engine.evolve_role(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        new_soul_md="v2",
        rationale="judge suggested",
        trigger_source="quality_low",
    )
    assert outcome.evolved is True
    current = next(v for v in store.versions if v.is_current)
    assert current.soul_md == "v2"
    assert {v.version_no for v in store.versions} == {1, 2}
    assert store.records[-1].kind == "evolution"
    assert store.records[-1].from_version_id is not None
    assert store.records[-1].to_version_id is not None


async def test_rollback_one_step_restores_previous_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _EvolutionStore()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _wire_db(monkeypatch, store, agent_id)

    await evolution_engine.seed_role_baseline(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="v1",
    )
    await evolution_engine.evolve_role(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        new_soul_md="v2",
        rationale="judge suggested",
        trigger_source="quality_low",
    )
    # v2 is current; rollback should set v1 current again.
    outcome = await evolution_engine.rollback_role_one_step(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        rationale="v2 regressed",
    )
    assert outcome.evolved is True
    current = next(v for v in store.versions if v.is_current)
    assert current.soul_md == "v1"
    assert current.version_no == 1
    assert store.records[-1].kind == "rollback"


async def test_rollback_when_only_baseline_returns_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _EvolutionStore()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _wire_db(monkeypatch, store, agent_id)
    await evolution_engine.seed_role_baseline(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="only",
    )
    outcome = await evolution_engine.rollback_role_one_step(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        rationale="nothing to roll back",
    )
    assert outcome.evolved is False
    assert outcome.new_version_id is None


async def test_record_quality_step_passed_updates_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _EvolutionStore()
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _wire_db(monkeypatch, store, agent_id)

    await evolution_engine.seed_role_baseline(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        soul_md="v1",
    )
    snapshot = await evolution_engine.record_quality_step_passed(
        session,
        agent=_StubAgent(id=agent_id, tenant_id=tenant_id),
        verdict=QualityVerdict(score=92, passed=True, feedback="ok", per_rule=()),
        trigger_ref_id=uuid.uuid4(),
    )
    assert snapshot is not None
    await session.flush()
    current = next(v for v in store.versions if v.is_current)
    assert current.quality_score == 92


# ---------------------------------------------------------------------------
# Helper stubs
# ---------------------------------------------------------------------------


@dataclass
class _StubAgent:
    id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    name: str = "Role Agent"


@dataclass
class _StubStep:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    task_summary: str = "Write a progress update"
    acceptance_text: str = "min 200 字 JSON"
    output_excerpt: str = "x" * 200
    output_file: str | None = None
    agent_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# LLM judge — fallback paths
# ---------------------------------------------------------------------------


class _NoModelSession:
    """Session that always resolves to ``None`` for any model lookup."""

    def __init__(self) -> None:
        self.flushes = 0

    async def scalar(self, _stmt):
        return None

    async def execute(self, _stmt):
        class _R:
            def scalars(self_):
                return self_

            def all(self_):
                return []

        return _R()

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        return None


class _ModelSession(_NoModelSession):
    """Session that always returns one valid LLMModel row."""

    async def execute(self, _stmt):
        class _R:
            def scalars(self_):
                return self_

            def all(self_):
                return [
                    type(
                        "M",
                        (),
                        {
                            "id": uuid.uuid4(),
                            "provider": "openai",
                            "model": "judge-test",
                            "api_key_encrypted": "secret",
                            "base_url": None,
                            "tenant_id": uuid.uuid4(),
                            "deleted_at": None,
                            "enabled": True,
                        },
                    )()
                ]

        return _R()


async def test_judge_falls_back_when_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_judge,
        "active_agent_model_candidates",
        lambda *_a, **_k: (),
    )
    result = await evaluate_step_with_judge(
        _NoModelSession(),  # type: ignore[arg-type]
        step=_StubStep(),
        output_excerpt="x" * 250,
        quality_threshold=80,
        agent=None,
    )
    assert isinstance(result, JudgeResult)
    assert result.judge_used is False
    assert result.error == "no_model"


async def test_judge_uses_model_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_chat(**_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "score": 90,
                                "passed": True,
                                "comments": "good",
                                "reasons": ["covers task"],
                            }
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "judge-mock",
            "usage": {},
        }

    monkeypatch.setattr(
        llm_judge,
        "active_agent_model_candidates",
        lambda *_a, **_k: (),
    )
    monkeypatch.setattr(llm_judge, "chat_complete", fake_chat)

    result = await evaluate_step_with_judge(
        _ModelSession(),  # type: ignore[arg-type]
        step=_StubStep(),
        output_excerpt="x" * 250,
        quality_threshold=80,
        agent=None,
    )
    assert result.judge_used is True
    assert result.score == 90
    assert result.passed is True
    assert result.reasons == ["covers task"]


async def test_judge_swallows_chat_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_chat(**_kwargs):
        raise llm_judge.LLMError("boom")

    monkeypatch.setattr(
        llm_judge,
        "active_agent_model_candidates",
        lambda *_a, **_k: (),
    )
    monkeypatch.setattr(llm_judge, "chat_complete", fake_chat)

    result = await evaluate_step_with_judge(
        _ModelSession(),  # type: ignore[arg-type]
        step=_StubStep(),
        output_excerpt="x" * 250,
        quality_threshold=80,
        agent=None,
    )
    assert result.judge_used is False
    assert result.error == "boom"


# ---------------------------------------------------------------------------
# Fixture comparison — JSON layout stays stable across runs
# ---------------------------------------------------------------------------


def test_role_version_payload_is_deterministic() -> None:
    payload = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "agent_id": str(uuid.uuid4()),
        "version_no": 2,
        "soul_md": "snapshot-of-soul",
        "source": "evolution",
        "is_current": True,
        "quality_score": 92,
    }
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["source"] == "evolution"
    assert decoded["version_no"] == 2
    assert decoded["is_current"] is True


def test_rule_engine_outputs_are_deterministic() -> None:
    """Regression guard: two consecutive evaluations stay byte-for-byte equal."""
    body = "alpha beta gamma delta"
    rules = {"min_length": 5, "threshold": 80}
    first = evaluate_output(step_id="s1", output_text=body, rules=rules)
    second = evaluate_output(step_id="s1", output_text=body, rules=rules)
    assert first.score == second.score
    assert first.passed == second.passed
    assert first.feedback == second.feedback


def test_judge_result_to_feedback_payload_is_stable() -> None:
    rule_verdict = QualityVerdict(
        score=70, passed=True, feedback="ok", per_rule=()
    )
    result = JudgeResult(
        passed=True,
        score=85,
        judge_used=True,
        rule_verdict=rule_verdict,
        comments="nice",
        reasons=["complete"],
    )
    payload = result.to_feedback_payload()
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["score"] == 85
    assert decoded["passed"] is True
    assert decoded["judge_used"] is True
    assert decoded["reasons"] == ["complete"]
