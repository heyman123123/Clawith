"""Unit coverage for leader wake copy and daily digest enqueue."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.agent_runtime.group_context_builder import _leader_workflow_instruction
from app.services.group_workflow import daily_digest
from app.services.group_workflow import worker
from app.services.group_workflow.worker import build_leader_wake_content


def test_approval_wake_mentions_human_confirm_targets() -> None:
    pid = str(uuid.uuid4())
    content = build_leader_wake_content(
        {
            "kind": "approval_required",
            "stage_title": "验收",
            "item_title": "合并报告",
            "confirm_targets": [{"participant_id": pid, "display_name": "Alice"}],
        }
    )
    assert "需确认" in content
    assert "@Alice" in content
    assert "at 工具" in content
    assert "invalid_group_at" in content
    assert "不要等待心跳" in content


def test_approval_wake_defers_to_decision_maker() -> None:
    dm_id = str(uuid.uuid4())
    content = build_leader_wake_content(
        {
            "kind": "approval_required",
            "stage_title": "验收",
            "decision_maker": {"participant_id": dm_id, "display_name": "决策者小D"},
            "confirm_targets": [],
        }
    )
    assert "待决策者拍板" in content
    assert "决策者小D" in content
    assert "不要自行向人类征求项目级拍板" in content
    assert "at 工具" in content


def test_member_progress_wake_asks_leader_to_continue() -> None:
    content = build_leader_wake_content(
        {
            "kind": "member_progress",
            "stage_title": "开发",
            "item_title": "接口联调",
            "actor_display_name": "Morty",
        }
    )
    assert "成员进度" in content
    assert "Morty" in content
    assert "@决策者" in content or "决策者" in content


def test_decision_resolved_wake_has_conclusion() -> None:
    content = build_leader_wake_content(
        {
            "kind": "decision_resolved",
            "stage_title": "验收",
            "decision_title": "进入联调",
            "decision_status": "auto_applied",
            "decision_summary": "证据齐全",
        }
    )
    assert "决策已定稿" in content
    assert "进入联调" in content
    assert "auto_applied" in content


def test_daily_digest_wake_is_confirmation_only() -> None:
    content = build_leader_wake_content(
        {
            "kind": "daily_digest",
            "stage_title": "开发",
            "summary": "工作流「Demo」进度：阶段[active 1]。",
        }
    )
    assert "日统计日报" in content
    assert "不驱动阶段推进" in content
    assert "Demo" in content


@pytest.mark.asyncio
async def test_action_claims_lock_only_event_rows_when_session_is_outer_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements = []

    @asynccontextmanager
    async def session_factory():
        db = SimpleNamespace()

        @asynccontextmanager
        async def begin():
            yield

        async def execute(statement):
            statements.append(statement)
            return SimpleNamespace(first=lambda: None)

        db.begin = begin
        db.execute = execute
        yield db

    monkeypatch.setattr(worker, "async_session", session_factory)

    assert await worker._claim_leader_action() is None
    assert await worker._claim_decision_action() is None

    sql = [
        str(statement.compile(dialect=postgresql.dialect()))
        for statement in statements
    ]
    assert len(sql) == 2
    assert all("FOR UPDATE OF group_workflow_events SKIP LOCKED" in statement for statement in sql)


@pytest.mark.asyncio
async def test_decision_action_executes_service_without_public_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval gates are executed by the worker, never echoed as a DM prompt."""
    event_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    group_id = uuid.uuid4()
    stage_id = uuid.uuid4()
    decision_maker_id = uuid.uuid4()
    event = SimpleNamespace(id=event_id, stage_id=stage_id)
    workflow = SimpleNamespace(id=workflow_id, group_id=group_id)
    group = SimpleNamespace(decision_maker_participant_id=decision_maker_id)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[event, workflow, group]),
    )

    @asynccontextmanager
    async def session_factory():
        @asynccontextmanager
        async def begin():
            yield

        db.begin = begin
        yield db

    monkeypatch.setattr(worker, "async_session", session_factory)
    monkeypatch.setattr(
        worker,
        "_claim_decision_action",
        AsyncMock(
            return_value=(
                event_id,
                uuid.uuid4(),
                decision_maker_id,
                uuid.uuid4(),
                {"kind": "approval_required", "stage_title": "技术评审"},
            )
        ),
    )
    settle = AsyncMock()
    monkeypatch.setattr(worker, "_settle", settle)
    apply = AsyncMock()
    from app.services.group_decision import service as decision_service

    monkeypatch.setattr(decision_service, "apply_routine_decision", apply)
    enqueue = AsyncMock()
    monkeypatch.setattr(worker.group_message_service, "enqueue_group_message", enqueue)

    assert await worker.dispatch_decision_actions_once() is True

    apply.assert_awaited_once_with(
        db,
        group_id=group_id,
        title="阶段「技术评审」常规确认",
        summary="阶段「技术评审」证据已齐，系统已自动执行常规决策并推进后续工作。",
        workflow_id=workflow_id,
        stage_id=stage_id,
    )
    enqueue.assert_not_awaited()
    settle.assert_awaited_once_with(event_id, dispatched=True)


@pytest.mark.asyncio
async def test_daily_digest_skips_when_idempotency_key_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        name="Demo",
        current_stage_id=None,
        leader_participant_id=uuid.uuid4(),
        status="active",
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [workflow]))
        ),
        scalar=AsyncMock(return_value=uuid.uuid4()),
    )

    @asynccontextmanager
    async def session_factory():
        @asynccontextmanager
        async def begin():
            yield

        db.begin = begin
        yield db

    monkeypatch.setattr(daily_digest, "async_session", session_factory)
    event = AsyncMock()
    monkeypatch.setattr(daily_digest, "_event", event)

    created = await daily_digest.enqueue_daily_digests_once(now=datetime(2026, 7, 30, tzinfo=UTC))

    assert created == 0
    event.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_digest_creates_pending_leader_action(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        name="Demo",
        current_stage_id=None,
        leader_participant_id=uuid.uuid4(),
        status="active",
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [workflow])),
                SimpleNamespace(all=lambda: [("active", 1)]),
                SimpleNamespace(all=lambda: [("done", 2), ("blocked", 1)]),
            ]
        ),
        scalar=AsyncMock(return_value=None),
    )

    @asynccontextmanager
    async def session_factory():
        @asynccontextmanager
        async def begin():
            yield

        db.begin = begin
        yield db

    monkeypatch.setattr(daily_digest, "async_session", session_factory)
    monkeypatch.setattr(daily_digest, "_human_confirm_targets", AsyncMock(return_value=[]))
    event = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(daily_digest, "_event", event)

    created = await daily_digest.enqueue_daily_digests_once(now=datetime(2026, 7, 30, tzinfo=UTC))

    assert created == 1
    payload = event.await_args.kwargs["payload"]
    assert payload["kind"] == "daily_digest"
    assert payload["day"] == "2026-07-30"
    assert event.await_args.kwargs["dispatch"] is True
    assert event.await_args.kwargs["idempotency_key"] == "daily_digest:2026-07-30"


def test_leader_instruction_requires_immediate_human_ping() -> None:
    text = _leader_workflow_instruction(
        {
            "kind": "approval_required",
            "confirm_targets": [{"display_name": "Bob"}],
        }
    )
    assert "Never wait for heartbeat" in text
    assert "@Bob" in text
    assert "decision maker" in text
    assert "`at` tool" in text
    assert "admin" in text.lower() or "humans" in text


def test_leader_instruction_pings_decision_maker_on_approval() -> None:
    dm_id = str(uuid.uuid4())
    text = _leader_workflow_instruction(
        {
            "kind": "approval_required",
            "decision_maker": {"participant_id": dm_id, "display_name": "小D"},
            "confirm_targets": [],
        }
    )
    assert "Never wait for heartbeat" in text
    assert dm_id in text
    assert "@小D" in text
    assert "group_decision_classify_and_act" in text
    assert "Do NOT ask admin" in text


def test_leader_instruction_stage_activated_relies_on_ready_task_dispatch() -> None:
    text = _leader_workflow_instruction({"kind": "stage_activated"})
    assert "dispatched automatically" in text
    assert "task-bound" in text
