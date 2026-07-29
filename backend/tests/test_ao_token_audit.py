"""Unit tests for step → workflow token rollup (需求 §4.8 / §8.6)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.ao.token_audit import (
    TokenTotals,
    _safe_int,
    apply_step_token_usage,
    get_workflow_token_report,
    recompute_workflow_token_totals,
)


def test_safe_int_clamps_and_defaults() -> None:
    assert _safe_int(None) == 0
    assert _safe_int(-3) == 0
    assert _safe_int("12") == 12
    assert _safe_int("x") == 0


class _FakeAuditDB:
    def __init__(self, workflow, steps: list) -> None:
        self.workflow = workflow
        self.steps = steps
        self.flushed = 0

    async def flush(self) -> None:
        self.flushed += 1

    async def get(self, _model, pk):
        if self.workflow is not None and pk == self.workflow.id:
            return self.workflow
        return None

    async def execute(self, _stmt):
        total_in = sum(int(s.input_tokens or 0) for s in self.steps)
        total_out = sum(int(s.output_tokens or 0) for s in self.steps)
        used = sum(
            1
            for s in self.steps
            if s.input_tokens is not None or s.output_tokens is not None
        )
        return SimpleNamespace(one=lambda: (total_in, total_out, used))

    async def scalar(self, _stmt):
        return self.workflow

    async def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: list(self.steps))


@pytest.mark.asyncio
async def test_apply_step_token_usage_updates_step_and_workflow_totals() -> None:
    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        total_input_tokens=0,
        total_output_tokens=0,
    )
    step_a = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="clarify",
        input_tokens=None,
        output_tokens=None,
        status="succeeded",
        step_order=0,
    )
    step_b = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="execute",
        input_tokens=50,
        output_tokens=20,
        status="succeeded",
        step_order=1,
    )
    db = _FakeAuditDB(workflow, [step_a, step_b])

    totals = await apply_step_token_usage(
        db,
        step=step_a,
        input_tokens=100,
        output_tokens=40,
    )

    assert step_a.input_tokens == 100
    assert step_a.output_tokens == 40
    assert totals == TokenTotals(
        workflow_id=workflow_id,
        total_input_tokens=150,
        total_output_tokens=60,
        step_count_with_usage=2,
    )
    assert workflow.total_input_tokens == 150
    assert workflow.total_output_tokens == 60
    assert db.flushed >= 2


@pytest.mark.asyncio
async def test_recompute_is_idempotent_when_recollecting_same_step() -> None:
    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        total_input_tokens=0,
        total_output_tokens=0,
    )
    step = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="review",
        input_tokens=10,
        output_tokens=5,
        status="succeeded",
        step_order=2,
    )
    db = _FakeAuditDB(workflow, [step])

    first = await apply_step_token_usage(
        db, step=step, input_tokens=10, output_tokens=5
    )
    second = await apply_step_token_usage(
        db, step=step, input_tokens=10, output_tokens=5
    )

    assert first.total_input_tokens == second.total_input_tokens == 10
    assert first.total_output_tokens == second.total_output_tokens == 5


@pytest.mark.asyncio
async def test_get_workflow_token_report_tenant_miss() -> None:
    db = _FakeAuditDB(None, [])
    report = await get_workflow_token_report(
        db, workflow_id=uuid.uuid4(), tenant_id=uuid.uuid4()
    )
    assert report["ok"] is False
    assert report["error"] == "workflow_not_found"


@pytest.mark.asyncio
async def test_get_workflow_token_report_lists_steps() -> None:
    workflow_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        tenant_id=tenant_id,
        total_input_tokens=30,
        total_output_tokens=12,
    )
    steps = [
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            step_key="clarify",
            input_tokens=10,
            output_tokens=4,
            status="succeeded",
            step_order=0,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            step_key="execute",
            input_tokens=20,
            output_tokens=8,
            status="succeeded",
            step_order=1,
        ),
    ]
    db = _FakeAuditDB(workflow, steps)
    report = await get_workflow_token_report(
        db, workflow_id=workflow_id, tenant_id=tenant_id
    )
    assert report["ok"] is True
    assert report["total_input_tokens"] == 30
    assert report["total_output_tokens"] == 12
    assert len(report["steps"]) == 2


@pytest.mark.asyncio
async def test_recompute_with_missing_workflow_still_returns_totals() -> None:
    workflow_id = uuid.uuid4()
    step = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        input_tokens=7,
        output_tokens=3,
    )
    db = _FakeAuditDB(None, [step])
    totals = await recompute_workflow_token_totals(db, workflow_id=workflow_id)
    assert totals.total_input_tokens == 7
    assert totals.total_output_tokens == 3
