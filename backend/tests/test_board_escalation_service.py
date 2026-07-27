"""Board escalation service tests."""

from __future__ import annotations

import uuid
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.board_escalation import BoardEscalation
from app.services.board_escalation_service import (
    DECISION_ESCALATION_PROMPT_SNIPPET,
    BOARD_SECRETARY_RESOLUTION_PROMPT_SNIPPET,
    apply_board_resolution,
    build_board_resolution_sync_content,
    build_escalation_case_brief,
    extract_board_resolution,
    extract_escalation_payload,
    open_board_escalation,
    parse_escalation_payload,
    process_shareholder_escalation_output,
)
from app.services.decision_record_service import process_decision_group_agent_output
from app.services.decision_sync_content import decision_summary_ready_for_task_dispatch


def test_parse_escalation_payload():
    raw = {
        "escalation_needed": True,
        "unresolved_points": ["budget"],
        "options": [{"id": "A", "summary": "cut"}],
    }
    parsed = parse_escalation_payload(raw)
    assert parsed["escalation_needed"] is True
    assert parsed["unresolved_points"] == ["budget"]
    assert parsed["options"] == [{"id": "A", "summary": "cut"}]


def test_parse_escalation_payload_rejects_missing_points():
    with pytest.raises(ValueError, match="unresolved_points"):
        parse_escalation_payload({"escalation_needed": True, "unresolved_points": []})


def test_extract_escalation_payload_from_json_fence():
    text = """Review stalled.

```json
{
  "escalation_needed": true,
  "unresolved_points": ["scope"],
  "options": [{"id": "A", "summary": "Reduce scope"}]
}
```
"""
    payload = extract_escalation_payload(text)
    assert payload is not None
    assert payload["escalation_needed"] is True
    assert payload["unresolved_points"] == ["scope"]


def test_extract_board_resolution_from_fence():
    text = """```json
{
  "board_resolution": {
    "summary": "Proceed with A",
    "chosen_option_id": "A",
    "constraints": ["Cap spend"],
    "authority_granted": "PM may re-plan"
  }
}
```"""
    resolution = extract_board_resolution(text)
    assert resolution is not None
    assert resolution["board_resolution"]["summary"] == "Proceed with A"
    assert resolution["board_resolution"]["chosen_option_id"] == "A"


def test_prompt_snippet_constants_documented():
    assert "escalation_needed" in DECISION_ESCALATION_PROMPT_SNIPPET
    assert "board_resolution" in BOARD_SECRETARY_RESOLUTION_PROMPT_SNIPPET


def test_decision_summary_ready_for_task_dispatch_blocks_escalation():
    assert decision_summary_ready_for_task_dispatch({"summary": "Go"}) is True
    assert decision_summary_ready_for_task_dispatch({"escalation_needed": True}) is False


def test_build_escalation_case_brief_includes_marker():
    escalation_id = uuid.uuid4()
    content = build_escalation_case_brief(
        workflow_name="Alpha",
        escalation_id=escalation_id,
        payload={
            "unresolved_points": ["budget"],
            "options": [{"id": "A", "summary": "cut"}],
        },
    )
    assert f"<!--board_escalation:{escalation_id}-->" in content
    assert "budget" in content
    assert "Board Secretary" in content


def test_build_board_resolution_sync_content_includes_marker():
    escalation_id = uuid.uuid4()
    content = build_board_resolution_sync_content(
        escalation_id=escalation_id,
        resolution={"board_resolution": {"summary": "Approved", "chosen_option_id": "A"}},
    )
    assert f"<!--board_resolution:{escalation_id}-->" in content
    assert "Approved" in content


@pytest.mark.asyncio
async def test_open_board_escalation_idempotent(monkeypatch):
    tenant_id = uuid.uuid4()
    decision_session_id = uuid.uuid4()
    existing = BoardEscalation(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        decision_group_id=uuid.uuid4(),
        decision_session_id=decision_session_id,
        shareholder_group_id=uuid.uuid4(),
        shareholder_session_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        status="open",
        escalation_payload={"escalation_needed": True, "unresolved_points": ["x"], "options": []},
    )

    class _DB:
        async def scalar(self, _statement):
            return existing

        async def get(self, *_args, **_kwargs):
            return None

        async def flush(self):
            return None

    called = {"ensure": 0}

    async def fake_ensure(*_args, **_kwargs):
        called["ensure"] += 1
        raise AssertionError("should not provision when open row exists")

    mock_shareholder = MagicMock()
    mock_shareholder.ensure_shareholder_group = fake_ensure
    monkeypatch.setitem(sys.modules, "app.services.shareholder_group_seeder", mock_shareholder)

    result = await open_board_escalation(
        _DB(),
        tenant_id=tenant_id,
        decision_group_id=uuid.uuid4(),
        decision_session_id=decision_session_id,
        workflow_id=uuid.uuid4(),
        payload={"escalation_needed": True, "unresolved_points": ["budget"], "options": []},
        creator_id=uuid.uuid4(),
    )
    assert result is existing
    assert called["ensure"] == 0


@pytest.mark.asyncio
async def test_open_board_escalation_posts_case_and_mentions_secretary(monkeypatch):
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    decision_group_id = uuid.uuid4()
    decision_session_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    shareholder_group_id = uuid.uuid4()
    primary_session_id = uuid.uuid4()
    shareholder_session_id = uuid.uuid4()
    secretary_participant_id = uuid.uuid4()

    workflow = SimpleNamespace(id=workflow_id, name="Project Z")
    shareholder_group = SimpleNamespace(id=shareholder_group_id)
    primary_session = SimpleNamespace(id=primary_session_id)
    shareholder_session = SimpleNamespace(id=shareholder_session_id)
    secretary_participant = SimpleNamespace(id=secretary_participant_id)
    creator = SimpleNamespace(display_name="Admin", avatar_url=None)
    creator_participant = SimpleNamespace(id=uuid.uuid4())

    added: list[BoardEscalation] = []

    class _DB:
        async def scalar(self, _statement):
            return None

        async def get(self, model, obj_id):
            if model.__name__ == "ProjectWorkflow":
                return workflow
            if model.__name__ == "User" and obj_id == creator_id:
                return creator
            if model.__name__ == "Tenant":
                return SimpleNamespace(default_model_id=None)
            return None

        def add(self, obj):
            added.append(obj)

        async def flush(self):
            return None

    mock_shareholder = MagicMock()
    mock_shareholder.ensure_shareholder_group = AsyncMock(return_value=shareholder_group)
    monkeypatch.setitem(sys.modules, "app.services.shareholder_group_seeder", mock_shareholder)
    monkeypatch.setattr(
        "app.services.board_escalation_service._default_shareholder_session",
        AsyncMock(return_value=primary_session),
    )
    monkeypatch.setattr(
        "app.services.board_escalation_service._board_secretary_participant",
        AsyncMock(return_value=secretary_participant),
    )
    monkeypatch.setattr(
        "app.services.board_escalation_service.get_or_create_user_participant",
        AsyncMock(return_value=creator_participant),
    )
    monkeypatch.setattr(
        "app.services.board_escalation_service.group_chat_service.create_group_session",
        AsyncMock(return_value=shareholder_session),
    )
    enqueue = AsyncMock()
    mock_gms = MagicMock()
    mock_gms.enqueue_group_message = enqueue
    monkeypatch.setitem(sys.modules, "app.services.group_message_service", mock_gms)

    await open_board_escalation(
        _DB(),
        tenant_id=tenant_id,
        decision_group_id=decision_group_id,
        decision_session_id=decision_session_id,
        workflow_id=workflow_id,
        payload={
            "escalation_needed": True,
            "unresolved_points": ["budget"],
            "options": [{"id": "A", "summary": "cut"}],
        },
        creator_id=creator_id,
    )

    assert len(added) == 1
    assert added[0].status == "open"
    assert added[0].shareholder_session_id == shareholder_session_id
    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["mention_participant_ids"] == [secretary_participant_id]
    assert kwargs["project_task_dispatch"] is False
    assert "budget" in kwargs["content"]


@pytest.mark.asyncio
async def test_apply_resolution_does_not_call_task_mutate(monkeypatch):
    escalation_id = uuid.uuid4()
    decision_session_id = uuid.uuid4()
    decision_group_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    escalation = BoardEscalation(
        id=escalation_id,
        tenant_id=tenant_id,
        decision_group_id=decision_group_id,
        decision_session_id=decision_session_id,
        shareholder_group_id=uuid.uuid4(),
        shareholder_session_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        status="open",
        escalation_payload={"escalation_needed": True, "unresolved_points": ["x"], "options": []},
    )
    decision_group = SimpleNamespace(id=decision_group_id, owner_agent_id=uuid.uuid4(), deleted_at=None)
    sender = SimpleNamespace(id=uuid.uuid4())

    class _DB:
        async def get(self, model, obj_id):
            if model is BoardEscalation and obj_id == escalation_id:
                return escalation
            if model.__name__ == "Group" and obj_id == decision_group_id:
                return decision_group
            return SimpleNamespace(id=obj_id, name="Board Secretary", deleted_at=None, avatar_url=None)

        async def flush(self):
            return None

    monkeypatch.setattr(
        "app.services.board_escalation_service._system_sender_participant",
        AsyncMock(return_value=sender),
    )
    enqueue = AsyncMock()
    mock_gms = MagicMock()
    mock_gms.enqueue_group_message = enqueue
    monkeypatch.setitem(sys.modules, "app.services.group_message_service", mock_gms)

    result = await apply_board_resolution(
        _DB(),
        escalation_id=escalation_id,
        resolution={"board_resolution": {"summary": "Choose A", "chosen_option_id": "A"}},
    )

    assert result.status == "resolved"
    assert result.board_resolution is not None
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["session_id"] == decision_session_id
    assert enqueue.await_args.kwargs["project_task_dispatch"] is False


def test_apply_board_resolution_source_does_not_touch_task_paths():
    source = Path("app/services/board_escalation_service.py").read_text()
    assert "advance_project_task" not in source
    assert "dispatch_decision_to_project_leader" not in source
    assert "project_task_dispatch=False" in source


@pytest.mark.asyncio
async def test_process_shareholder_escalation_output_applies_resolution(monkeypatch):
    escalation_id = uuid.uuid4()
    shareholder_session_id = uuid.uuid4()
    escalation = BoardEscalation(
        id=escalation_id,
        tenant_id=uuid.uuid4(),
        decision_group_id=uuid.uuid4(),
        decision_session_id=uuid.uuid4(),
        shareholder_group_id=uuid.uuid4(),
        shareholder_session_id=shareholder_session_id,
        workflow_id=uuid.uuid4(),
        status="open",
        escalation_payload={"escalation_needed": True, "unresolved_points": ["x"], "options": []},
    )

    class _DB:
        async def scalar(self, _statement):
            return escalation

    apply_mock = AsyncMock(return_value=escalation)
    monkeypatch.setattr("app.services.board_escalation_service.apply_board_resolution", apply_mock)

    text = '```json\n{"board_resolution": {"summary": "Go", "chosen_option_id": "A"}}\n```'
    result = await process_shareholder_escalation_output(
        _DB(),
        shareholder_session_id=shareholder_session_id,
        text=text,
    )
    assert result is escalation
    apply_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_decision_group_output_routes_escalation(monkeypatch):
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        decision_group_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
    )
    escalation = SimpleNamespace(id=uuid.uuid4())
    open_mock = AsyncMock(return_value=escalation)
    finalize_mock = AsyncMock()
    monkeypatch.setattr("app.services.board_escalation_service.open_board_escalation", open_mock)
    monkeypatch.setattr("app.services.decision_record_service.finalize_decision_record", finalize_mock)

    text = """```json
{
  "escalation_needed": true,
  "unresolved_points": ["budget"],
  "options": [{"id": "A", "summary": "cut"}]
}
```"""
    result = await process_decision_group_agent_output(
        MagicMock(),
        tenant_id=uuid.uuid4(),
        workflow=workflow,
        decision_session_id=uuid.uuid4(),
        project_session_id=uuid.uuid4(),
        text=text,
        participants=[],
    )
    assert result is escalation
    open_mock.assert_awaited_once()
    finalize_mock.assert_not_awaited()
