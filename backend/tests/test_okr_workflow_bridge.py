"""Unit coverage for OKR project-progress push helpers and bridge gating."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import okr_settings_helpers as helpers
from app.services import okr_workflow_bridge as bridge
from app.services.okr_daily_collection import _human_request_message


def test_normalize_defaults_and_filters_unknown_events() -> None:
    assert helpers.normalize_push_cadence(None) == "both"
    assert helpers.normalize_push_cadence("workflow") == "workflow"
    assert helpers.normalize_workflow_events(None) == ["stage_completed", "workflow_completed"]
    assert helpers.normalize_workflow_events(["stage_completed", "nope", "approval_required"]) == [
        "stage_completed",
        "approval_required",
    ]
    gid = str(uuid.uuid4())
    assert helpers.normalize_excluded_group_ids([gid, "bad", gid]) == [gid]


def test_calendar_collection_respects_cadence() -> None:
    settings = SimpleNamespace(enabled=True, daily_report_enabled=True, push_cadence="workflow")
    assert helpers.calendar_collection_active(settings) is False
    settings.push_cadence = "both"
    assert helpers.calendar_collection_active(settings) is True


def test_prefill_is_appended_to_human_request() -> None:
    text = _human_request_message("Alice", __import__("datetime").date(2026, 7, 30), prefill="阶段：验收")
    assert "Alice" in text
    assert "阶段：验收" in text
    assert "项目进度参考" in text


@pytest.mark.asyncio
async def test_bridge_skips_excluded_group(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    settings = SimpleNamespace(
        enabled=True,
        push_cadence="both",
        workflow_trigger_events=["stage_completed"],
        excluded_group_ids=[str(group_id)],
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, *_args, **_kwargs):
            return settings

        async def commit(self):
            return None

    monkeypatch.setattr(bridge, "async_session", lambda: Session())
    collect = AsyncMock()
    monkeypatch.setattr(bridge.okr_daily_collection, "trigger_workflow_collection_for_group", collect)

    result = await bridge.on_workflow_event(
        tenant_id=tenant_id,
        group_id=group_id,
        event_key="stage_completed",
        workflow_id=uuid.uuid4(),
        stage_id=uuid.uuid4(),
    )
    assert result is None
    collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_bridge_skips_unselected_event(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        enabled=True,
        push_cadence="both",
        workflow_trigger_events=["stage_completed"],
        excluded_group_ids=[],
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, *_args, **_kwargs):
            return settings

        async def commit(self):
            return None

    monkeypatch.setattr(bridge, "async_session", lambda: Session())
    collect = AsyncMock()
    monkeypatch.setattr(bridge.okr_daily_collection, "trigger_workflow_collection_for_group", collect)

    result = await bridge.on_workflow_event(
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        event_key="stage_activated",
        workflow_id=uuid.uuid4(),
    )
    assert result is None
    collect.assert_not_awaited()
