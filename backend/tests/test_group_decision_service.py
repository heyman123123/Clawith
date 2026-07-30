"""Unit tests for group decision service helpers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.group_decision import service as decision_service


def test_normalize_category_sensitive_forces_uncertain() -> None:
    assert decision_service.normalize_category("routine", title="本周排期", summary="无") == "routine"
    assert (
        decision_service.normalize_category("routine", title="申请打款", summary="供应商")
        == "uncertain"
    )
    assert decision_service.normalize_category("bogus") == "uncertain"


@pytest.mark.asyncio
async def test_resolve_report_recipients_modes() -> None:
    group_id = uuid.uuid4()
    manager_id = uuid.uuid4()
    other_id = uuid.uuid4()

    async def execute_all_managers(stmt):  # noqa: ARG001
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [manager_id]))

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute_all_managers))
    group = SimpleNamespace(id=group_id, decision_report_participant_ids=None)
    assert await decision_service.resolve_report_recipients(db, group) == [manager_id]

    group_none = SimpleNamespace(id=group_id, decision_report_participant_ids=[])
    assert await decision_service.resolve_report_recipients(db, group_none) == []

    async def execute_explicit(stmt):  # noqa: ARG001
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [other_id]))

    db2 = SimpleNamespace(execute=AsyncMock(side_effect=execute_explicit))
    group_custom = SimpleNamespace(
        id=group_id, decision_report_participant_ids=[str(other_id), "not-a-uuid"]
    )
    assert await decision_service.resolve_report_recipients(db2, group_custom) == [other_id]


@pytest.mark.asyncio
async def test_approve_rejects_second_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    decision_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = uuid.uuid4()
    decision = SimpleNamespace(
        id=decision_id,
        group_id=group_id,
        status="approved",
        stage_id=None,
        decision_maker_participant_id=uuid.uuid4(),
        approver_participant_id=uuid.uuid4(),
        decided_at=None,
        report_sent_at=None,
        title="t",
        summary="s",
        category="finance",
    )
    db = MagicMock()
    db.scalar = AsyncMock(return_value=decision)
    monkeypatch.setattr(
        decision_service,
        "_require_human_manager",
        AsyncMock(return_value=SimpleNamespace(id=actor, type="user")),
    )
    with pytest.raises(decision_service.GroupDecisionError) as exc:
        await decision_service.approve_decision(db, decision_id=decision_id, actor_participant_id=actor)
    assert exc.value.code == "decision_not_pending"


def test_build_decision_wake_content() -> None:
    from app.services.group_decision.wake import build_decision_wake_content

    text = build_decision_wake_content({"kind": "approval_required", "stage_title": "验收"})
    assert "验收" in text
    assert "group_decision_classify_and_act" in text
