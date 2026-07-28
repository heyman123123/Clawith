"""P2.2 tests — rule-based quality engine + DB write path."""

from __future__ import annotations

import uuid

import pytest

from app.services.ao import quality_engine
from app.services.ao.quality_engine import (
    FAIL_STATUS,
    RETRY_STATUS,
    TERMINAL_PASS_STATUS,
    run_quality_check,
)
from app.services.ao.quality_rules import RULE_CATALOG, evaluate_output

# ---------------------------------------------------------------------------
# Rule engine — pure functions
# ---------------------------------------------------------------------------


def test_catalog_exposes_four_rules() -> None:
    keys = {rule.key for rule in RULE_CATALOG}
    assert keys == {"min_length", "must_mention", "no_placeholder", "structure"}


def test_min_length_rule_blocks_short_text() -> None:
    short = evaluate_output(step_id="x", output_text="hi", rules={"min_length": 5})
    assert short.score < 100
    long_enough = evaluate_output(
        step_id="x", output_text="a" * 200, rules={"min_length": 50}
    )
    assert long_enough.passed is True


def test_must_mention_rule_fails_when_missing() -> None:
    verdict_ok = evaluate_output(
        step_id="abc123",
        output_text="lorem ipsum abc123 dolor sit amet",
        rules={"must_mention": ["abc123"]},
    )
    rule_results = {r.rule: r for r in verdict_ok.per_rule}
    assert rule_results["must_mention"].ok is True

    verdict_missing = evaluate_output(
        step_id="zzz",
        output_text="lorem ipsum dolor sit amet",
        rules={"must_mention": ["absent"]},
    )
    rule_results_missing = {r.rule: r for r in verdict_missing.per_rule}
    assert rule_results_missing["must_mention"].ok is False


def test_no_placeholder_rule() -> None:
    verdict = evaluate_output(step_id="x", output_text="clean output without markers")
    rule_results = {r.rule: r for r in verdict.per_rule}
    assert rule_results["no_placeholder"].ok is True

    verdict2 = evaluate_output(step_id="x", output_text="still 待补 this")
    rule_results2 = {r.rule: r for r in verdict2.per_rule}
    assert rule_results2["no_placeholder"].ok is False


def test_structure_rule_handles_fenced_json() -> None:
    import json

    body = "```json\n" + json.dumps({"score": 70}) + "\n```"
    verdict = evaluate_output(step_id="x", output_text=body, rules={"requires_json": True})
    rule_results = {r.rule: r for r in verdict.per_rule}
    assert rule_results["structure"].ok is True


def test_full_pass_when_all_rules_ok() -> None:
    body = " ".join(["x"] * 80)
    verdict = evaluate_output(step_id="abc", output_text=body, rules={"must_mention": ["abc"]})
    assert verdict.passed is True
    assert verdict.score >= 80


# ---------------------------------------------------------------------------
# DB write path — use lightweight stubs to avoid spinning up SQLAlchemy
# ---------------------------------------------------------------------------


class _StubStep:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.workflow_id = kw.get("workflow_id", uuid.uuid4())
        self.tenant_id = kw.get("tenant_id", uuid.uuid4())
        self.step_key = kw.get("step_key", "execute")
        self.output_excerpt = kw.get("output_excerpt", "x" * 200)
        self.retry_count = kw.get("retry_count", 0)
        self.max_retries = kw.get("max_retries", 2)
        self.quality_score = None
        self.quality_feedback = None
        self.status = "running"
        self.completed_at = None
        self.acceptance_text = kw.get("acceptance_text")
        self.started_at = None
        self.updated_at = None
        self.agent_id = kw.get("agent_id", None)
        self.task_summary = kw.get("task_summary", "")
        self.output_file = kw.get("output_file", None)
        self.input_tokens = None
        self.output_tokens = None


class _StubWorkflow:
    def __init__(self, quality_threshold=None):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.quality_threshold = quality_threshold
        self.last_event_at = None


class _StubDB:
    def __init__(self, step, workflow):
        self._step = step
        self._workflow = workflow
        self.flushed = 0

    async def scalar(self, _stmt):
        # The query is the most-recent-loaded object; tests load either
        # step or workflow first. We rely on call ordering by inspecting
        # the load target via the bound arguments.
        column = getattr(getattr(_stmt, "column_descriptions", lambda: [])(), 0, {})
        column_name = column.get("name") if isinstance(column, dict) else None
        if column_name == "id":
            entity = column.get("entity")
            if entity is not None and getattr(entity, "__name__", "") == "Agent":
                return None
        return self._step if self._step is not None else self._workflow

    async def execute(self, _stmt):
        # P3 LLM judge path uses ``db.execute`` for LLMModel fallback. Return
        # an object whose ``scalars()`` returns an empty iterator so the
        # judge falls back to the rule engine.
        return _EmptyResult()

    async def flush(self):
        self.flushed += 1


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


async def test_run_quality_check_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    step = _StubStep(output_excerpt="x" * 200)
    workflow = _StubWorkflow(quality_threshold=80)
    db = _StubDB(step, workflow)

    async def fake_load_step(_db, step_id):
        return step

    async def fake_load_workflow(_db, workflow_id):
        return workflow

    async def fake_write_asset(*args, **kwargs):
        return {"ok": True, "abs_path": "/tmp/x"}

    monkeypatch.setattr(quality_engine, "_load_step", fake_load_step)
    monkeypatch.setattr(quality_engine, "_load_workflow", fake_load_workflow)
    monkeypatch.setattr(quality_engine, "write_step_asset", fake_write_asset)

    outcome = await run_quality_check(
        db,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        step_id=step.id,
    )
    assert outcome.next_status == TERMINAL_PASS_STATUS
    assert outcome.verdict.score >= 80
    assert step.quality_score == outcome.verdict.score


async def test_run_quality_check_retries_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    step = _StubStep(output_excerpt="too short", retry_count=0)
    workflow = _StubWorkflow(quality_threshold=95)
    db = _StubDB(step, workflow)

    async def fake_load_step(_db, step_id):
        return step

    async def fake_load_workflow(_db, workflow_id):
        return workflow

    async def fake_write_asset(*args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(quality_engine, "_load_step", fake_load_step)
    monkeypatch.setattr(quality_engine, "_load_workflow", fake_load_workflow)
    monkeypatch.setattr(quality_engine, "write_step_asset", fake_write_asset)

    first = await run_quality_check(
        db,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        step_id=step.id,
    )
    assert first.next_status == RETRY_STATUS
    assert first.retry_count == 1

    # Simulate worker producing another low-quality output and the loop
    # re-running the check. With retry_count=1 and max_retries=2 we still
    # get one more RETRY before escalation.
    step.output_excerpt = "still too short"
    second = await run_quality_check(
        db,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        step_id=step.id,
    )
    assert second.next_status == RETRY_STATUS
    assert second.retry_count == 2

    third = await run_quality_check(
        db,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        step_id=step.id,
    )
    assert third.next_status == FAIL_STATUS


async def test_run_quality_check_missing_step(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_load_step(_db, step_id):
        return None

    monkeypatch.setattr(quality_engine, "_load_step", fake_load_step)

    with pytest.raises(ValueError):
        await run_quality_check(
            _StubDB(None, None),
            workflow_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            step_id=uuid.uuid4(),
        )