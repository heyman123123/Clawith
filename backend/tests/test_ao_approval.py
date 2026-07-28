"""P2.4 tests — approval node bridge, lightweight DB stubs."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import group_message_service
from app.services.ao import approval_node
from app.services.ao.scheduler_tools import AOIntegrationError

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubStep:
    def __init__(self):
        self.id = uuid.uuid4()
        self.workflow_id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.step_key = "review"
        self.status = "running"
        self.started_at = None
        self.updated_at = None


class _StubWorkflow:
    def __init__(self):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.group_id = uuid.uuid4()
        self.decision_group_id = None
        self.scheduler_agent_id = uuid.uuid4()


class _StubDecision:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.workflow_id = kw["workflow_id"]
        self.group_id = kw["group_id"]
        self.session_id = kw.get("session_id", uuid.uuid4())
        self.status = kw.get("status", "pending")
        self.response = None
        self.responded_at = None
        self.context = kw.get("context", "{}")
        self.title = "approval"


class _StubDB:
    def __init__(self, *, workflow=None, step=None, decision=None):
        self._workflow = workflow
        self._step = step
        self._decision = decision
        self.added: list[Any] = []
        self.flushed = 0

    async def scalar(self, stmt):
        desc = str(stmt).lower()
        if "project_workflows" in desc:
            return self._workflow
        if "workflow_run_steps" in desc:
            return self._step
        return None

    async def get(self, _cls, pk):
        if self._decision is not None and pk == self._decision.id:
            return self._decision
        if self._workflow is not None and pk == self._workflow.id:
            return self._workflow
        if self._step is not None and pk == self._step.id:
            return self._step
        return None

    def add(self, value):
        self.added.append(value)
        # We never want the production ProjectDecision type in the stub, but
        # approval_node builds real rows; capture anything tagged with the
        # _StubDecision class attribute (set below) or whose class name
        # matches ProjectDecision.
        if self._decision is None and getattr(value, "_stub_decision_marker", False):
            self._decision = value

    async def flush(self):
        self.flushed += 1


def _stub_session_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_trigger_approval_creates_decision_and_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _StubWorkflow()
    step = _StubStep()
    captured: dict[str, Any] = {}
    db = _StubDB(workflow=workflow, step=step)
    original_add = db.add

    def capturing_add(value):
        original_add(value)
        # Capture the first ProjectDecision-like row that lands.
        if "decision" not in captured:
            captured["decision"] = value

    db.add = capturing_add  # type: ignore[assignment]
    enqueued: list[dict] = []
    enqueued: list[dict] = []

    async def fake_resolve_session(db, *, group_id):
        return _stub_session_id()

    async def fake_load_approvers(db, *, approver_user_ids):
        return [SimpleNamespace(id=uuid.uuid4())]

    async def fake_load_sender(db, *, workflow):
        return uuid.uuid4()

    async def fake_enqueue(*args, **kwargs):
        class _Msg:
            id = uuid.uuid4()

        enqueued.append({"kwargs": kwargs})
        return type("Intake", (), {"message": _Msg()})()

    async def fake_load_step(db, *, workflow_id, step_id):
        return step

    monkeypatch.setattr(approval_node, "_resolve_active_session_id", fake_resolve_session)
    monkeypatch.setattr(approval_node, "_load_approver_participants", fake_load_approvers)
    monkeypatch.setattr(approval_node, "_load_sender_participant", fake_load_sender)
    monkeypatch.setattr(
        group_message_service, "enqueue_group_message", fake_enqueue
    )
    monkeypatch.setattr(approval_node, "_load_step", fake_load_step)

    approver_id = uuid.uuid4()
    result = await approval_node.trigger_approval_node(
        db,
        workflow_id=workflow.id,
        step_id=step.id,
        prompt="需要客户签字确认",
        approver_user_ids=[approver_id],
    )
    assert result["ok"] is True
    decision_id = uuid.UUID(result["decision_id"])
    assert "decision" in captured, "decision row should have been added"
    decision = captured["decision"]
    assert getattr(decision, "status", None) == "pending"
    assert step.status == "awaiting_approval"
    assert enqueued, "approval broadcast should enqueue a group message"
    assert f"<!--approval:{decision_id}-->" in enqueued[0]["kwargs"]["content"]


async def test_trigger_approval_requires_prompt_and_approver() -> None:
    workflow = _StubWorkflow()
    step = _StubStep()
    db = _StubDB(workflow=workflow, step=step)
    with pytest.raises(AOIntegrationError):
        await approval_node.trigger_approval_node(
            db,
            workflow_id=workflow.id,
            step_id=step.id,
            prompt="   ",
            approver_user_ids=[uuid.uuid4()],
        )
    with pytest.raises(AOIntegrationError):
        await approval_node.trigger_approval_node(
            db,
            workflow_id=workflow.id,
            step_id=step.id,
            prompt="needs sign-off",
            approver_user_ids=[],
        )


async def test_resolve_approval_approved_resumes_ao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _StubWorkflow()
    step = _StubStep()
    decision = _StubDecision(
        workflow_id=workflow.id,
        group_id=workflow.group_id,
        status="pending",
        context='{"step_id": "' + str(step.id) + '"}',
    )
    db = _StubDB(workflow=workflow, step=step, decision=decision)
    resume_calls: list[dict] = []

    async def fake_enqueue(*args, **kwargs):
        class _Msg:
            id = uuid.uuid4()

        return type("Intake", (), {"message": _Msg()})()

    async def fake_resolve_session(db, *, group_id):
        return _stub_session_id()

    async def fake_load_step(db, *, workflow_id, step_id):
        return step

    async def fake_resume(workflow_id: str, from_step: str, feedback: str | None = None):
        resume_calls.append(
            {"workflow_id": workflow_id, "from_step": from_step, "feedback": feedback}
        )
        return {"ok": True, "returncode": 0, "stdout": ""}

    monkeypatch.setattr(
        group_message_service, "enqueue_group_message", fake_enqueue
    )
    monkeypatch.setattr(approval_node, "ao_resume_from_step", fake_resume)
    monkeypatch.setattr(approval_node, "_resolve_active_session_id", fake_resolve_session)
    monkeypatch.setattr(approval_node, "_load_step", fake_load_step)

    resolved = await approval_node.resolve_approval(
        db,
        decision_id=decision.id,
        response_text="approved",
        approved=True,
    )
    assert resolved["ok"] is True
    assert resume_calls and resume_calls[0]["workflow_id"] == str(workflow.id)
    assert step.status == "running"
    assert decision.status == "answered"


async def test_resolve_approval_rejected_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _StubWorkflow()
    step = _StubStep()
    decision = _StubDecision(
        workflow_id=workflow.id,
        group_id=workflow.group_id,
        status="pending",
        context='{"step_id": "' + str(step.id) + '"}',
    )
    db = _StubDB(workflow=workflow, step=step, decision=decision)

    async def fake_enqueue(*args, **kwargs):
        class _Msg:
            id = uuid.uuid4()

        return type("Intake", (), {"message": _Msg()})()

    async def fake_resolve_session(db, *, group_id):
        return _stub_session_id()

    async def fake_load_step(db, *, workflow_id, step_id):
        return step

    async def fake_broadcast_rejection(*args, **kwargs):
        return None

    monkeypatch.setattr(
        group_message_service, "enqueue_group_message", fake_enqueue
    )
    monkeypatch.setattr(approval_node, "_resolve_active_session_id", fake_resolve_session)
    monkeypatch.setattr(approval_node, "_load_step", fake_load_step)
    monkeypatch.setattr(approval_node, "_broadcast_rejection", fake_broadcast_rejection)

    resolved = await approval_node.resolve_approval(
        db,
        decision_id=decision.id,
        response_text="no",
        approved=False,
    )
    assert resolved["ok"] is True
    assert step.status == "failed"
    assert decision.status == "answered"
    assert decision.response == "no"