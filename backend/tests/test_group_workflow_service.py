"""Evidence and approval transitions stay deterministic under retries."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.group_workflow import service


@pytest.mark.asyncio
async def test_submit_evidence_completes_item_before_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = uuid.uuid4()
    workflow = SimpleNamespace(id=uuid.uuid4(), leader_participant_id=actor, version=3)
    stage = SimpleNamespace(id=uuid.uuid4(), title="交付", requires_approval=False)
    item = SimpleNamespace(id=uuid.uuid4(), assignee_participant_id=actor, status="in_progress", evidence=[], blocked_reason=None, version=2)
    monkeypatch.setattr(service, "_locked_item", AsyncMock(return_value=(workflow, stage, item)))
    recorded = AsyncMock()
    monkeypatch.setattr(service, "_event", recorded)
    transition = SimpleNamespace(workflow=workflow, stage=stage, next_stage=None, leader_action=None)
    reconcile = AsyncMock(return_value=transition)
    monkeypatch.setattr(service, "_reconcile", reconcile)

    result = await service.submit_evidence(SimpleNamespace(), item_id=item.id, actor_participant_id=actor, evidence={"ref": "report.md"})

    assert result is transition
    assert item.status == "done"
    assert item.evidence == [{"ref": "report.md"}]
    assert item.version == 3
    assert workflow.version == 4
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_stops_at_approval_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), version=4, status="active")
    stage = SimpleNamespace(id=uuid.uuid4(), title="验收", requires_approval=True, status="active")
    item = SimpleNamespace(status="done")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [item]))))
    action = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(service, "_leader_action", AsyncMock(return_value=action))
    monkeypatch.setattr(service, "_decision_action", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_workflow_okr_requires_human_confirm", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_notify_okr", AsyncMock())

    result = await service._reconcile(db, workflow=workflow, stage=stage)

    assert stage.status == "awaiting_approval"
    assert workflow.status == "awaiting_approval"
    assert result.next_stage is None
    assert result.leader_action is action


@pytest.mark.asyncio
async def test_reconcile_waits_for_confirm_when_okr_workflow_push_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), version=4, status="active")
    stage = SimpleNamespace(id=uuid.uuid4(), title="验收", requires_approval=False, status="active")
    item = SimpleNamespace(status="done")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [item]))))
    action = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(service, "_leader_action", AsyncMock(return_value=action))
    monkeypatch.setattr(service, "_decision_action", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_workflow_okr_requires_human_confirm", AsyncMock(return_value=True))
    notify = AsyncMock()
    monkeypatch.setattr(service, "_notify_okr", notify)

    result = await service._reconcile(db, workflow=workflow, stage=stage)

    assert stage.status == "awaiting_approval"
    assert workflow.status == "awaiting_approval"
    assert result.leader_action is action
    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "approval_required"


@pytest.mark.asyncio
async def test_complete_stage_okr_only_after_human_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        version=1,
        status="awaiting_approval",
        current_stage_id=None,
    )
    stage = SimpleNamespace(id=uuid.uuid4(), position=0, title="交付", status="awaiting_approval", completed_at=None)
    next_stage = SimpleNamespace(id=uuid.uuid4(), position=1, status="pending", started_at=None)
    action = SimpleNamespace(id=uuid.uuid4())

    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: next_stage))
    )
    monkeypatch.setattr(service, "_event", AsyncMock())
    monkeypatch.setattr(service, "_leader_action", AsyncMock(return_value=action))
    notify = AsyncMock()
    monkeypatch.setattr(service, "_notify_okr", notify)

    await service._complete_stage(db, workflow=workflow, stage=stage, source="workflow")
    assert notify.await_count >= 1
    assert all(call.kwargs.get("confirmed") is False for call in notify.await_args_list)

    notify.reset_mock()
    stage.status = "awaiting_approval"
    await service._complete_stage(db, workflow=workflow, stage=stage, source="human")
    assert any(call.kwargs.get("confirmed") is True for call in notify.await_args_list)