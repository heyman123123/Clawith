"""Recover durable workflow actions by activating the group leader once."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from sqlalchemy import select

from app.database import async_session
from app.models.chat_session import ChatSession
from app.models.group_workflow import GroupWorkflow, GroupWorkflowEvent
from app.models.participant import Participant
from app.services import group_message_service
from app.services.group_workflow import daily_digest

logger = logging.getLogger(__name__)

_DIGEST_SCAN_SECONDS = 60.0


def _confirm_hint(payload: dict[str, Any]) -> str:
    targets = payload.get("confirm_targets") or []
    if not isinstance(targets, list) or not targets:
        return "如需人类确认，请立刻在群内公开说明确认项并 @ 群管理员；同时可继续催成员补证据，禁止干等。"
    names = ", ".join(
        f"@{str(target.get('display_name') or '').strip()}"
        for target in targets
        if isinstance(target, dict) and str(target.get("display_name") or "").strip()
    )
    if not names:
        return "如需人类确认，请立刻在群内公开说明确认项并 @ 群管理员；同时可继续催成员补证据，禁止干等。"
    return f"请立刻在群内公开说明确认项并 {names}；同时可继续催成员补证据，禁止干等。"


def build_leader_wake_content(payload: dict[str, Any]) -> str:
    """Human-readable wake text keyed by leader_action kind."""
    kind = str(payload.get("kind") or "state_changed")
    stage = str(payload.get("stage_title") or "当前阶段")
    item = payload.get("item_title")
    item_part = f"；关联工作项：{item}" if item else ""
    confirm = _confirm_hint(payload)

    if kind == "approval_required":
        return (
            f"工作流推进指令（需人类确认）：阶段「{stage}」已齐备证据，等待管理员确认后才能进入下一阶段"
            f"{item_part}。{confirm} 不要等待心跳或定时；在确认前可继续催未完成证据。"
        )
    if kind == "blocker":
        return (
            f"工作流推进指令（阻塞）：阶段「{stage}」存在阻塞{item_part}。"
            f"请立刻公开处理阻塞或重新分派；{confirm}"
        )
    if kind == "stage_activated":
        return (
            f"工作流推进指令（阶段激活）：「{stage}」已激活{item_part}。"
            "请立刻在群内公开分派下一步可执行工作，按 SOP/证据推进，禁止按时间等待。"
        )
    if kind == "workflow_resumed":
        return (
            f"工作流推进指令（已恢复）：「{stage}」{item_part}。"
            "请立刻继续分派下一步并处理遗留阻塞，禁止按时间等待。"
        )
    if kind == "workflow_completed":
        return (
            f"工作流推进指令（已完成）：「{stage}」{item_part}。"
            "请在群内公开确认完成状态；无需再推进阶段。"
        )
    if kind == "daily_digest":
        summary = str(payload.get("summary") or "").strip()
        body = summary or f"阶段「{stage}」日统计摘要已生成。"
        return (
            f"【日统计日报】{body} "
            "本日报仅供群主/管理员确认进度，不驱动阶段推进。请审阅并公开确认。"
        )
    return (
        f"工作流推进指令（{kind}）：{stage}{item_part}。"
        f"请依据当前工作流状态立刻公开分发下一步或处理阻塞。{confirm}"
    )


async def _claim_leader_action() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None, dict] | None:
    async with async_session() as db, db.begin():
        result = await db.execute(
            select(GroupWorkflowEvent, GroupWorkflow, ChatSession.id)
            .join(GroupWorkflow, GroupWorkflow.id == GroupWorkflowEvent.workflow_id)
            .outerjoin(
                ChatSession,
                (ChatSession.group_id == GroupWorkflow.group_id)
                & (ChatSession.session_type == "group")
                & (ChatSession.is_primary.is_(True))
                & (ChatSession.deleted_at.is_(None)),
            )
            .where(
                GroupWorkflowEvent.event_type == "leader_action",
                GroupWorkflowEvent.dispatch_state == "pending",
            )
            .order_by(GroupWorkflowEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        event, workflow, session_id = row
        event.dispatch_state = "claimed"
        return event.id, workflow.tenant_id, workflow.leader_participant_id, session_id, dict(event.payload or {})


async def _settle(event_id: uuid.UUID, *, dispatched: bool) -> None:
    async with async_session() as db, db.begin():
        event = await db.scalar(select(GroupWorkflowEvent).where(GroupWorkflowEvent.id == event_id).with_for_update())
        if event is None or event.dispatch_state != "claimed":
            return
        if dispatched:
            from datetime import UTC, datetime
            event.dispatch_state = "dispatched"
            event.dispatched_at = datetime.now(UTC)
        else:
            event.dispatch_state = "pending"


async def dispatch_leader_actions_once() -> bool:
    """Dispatch one already-created action; never invent a time-based reminder."""
    claimed = await _claim_leader_action()
    if claimed is None:
        return False
    event_id, tenant_id, leader_participant_id, session_id, payload = claimed
    # A manually-created group can have no Agent leader. Keep the action in
    # history but do not leave the worker spinning indefinitely on it.
    if leader_participant_id is None or session_id is None:
        await _settle(event_id, dispatched=True)
        return True
    try:
        async with async_session() as db, db.begin():
            leader = await db.scalar(
                select(Participant).where(Participant.id == leader_participant_id)
            )
            if leader is None or leader.type != "agent":
                await _settle(event_id, dispatched=True)
                return True
            workflow = await db.scalar(
                select(GroupWorkflow).join(GroupWorkflowEvent, GroupWorkflowEvent.workflow_id == GroupWorkflow.id)
                .where(GroupWorkflowEvent.id == event_id)
            )
            if workflow is None:
                await _settle(event_id, dispatched=True)
                return True
            await group_message_service.enqueue_group_message(
                db,
                tenant_id=tenant_id,
                group_id=workflow.group_id,
                session_id=session_id,
                sender_participant_id=leader_participant_id,
                mention_participant_ids=[leader_participant_id],
                message_id=uuid.uuid5(uuid.NAMESPACE_URL, f"group-workflow-action:{event_id}"),
                content=build_leader_wake_content(payload),
            )
        await _settle(event_id, dispatched=True)
    except Exception:
        logger.exception("Group workflow leader action %s could not be dispatched", event_id)
        await _settle(event_id, dispatched=False)
    return True


async def _claim_decision_action() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None, dict] | None:
    from app.models.group import Group

    async with async_session() as db, db.begin():
        result = await db.execute(
            select(GroupWorkflowEvent, GroupWorkflow, Group.decision_maker_participant_id, ChatSession.id)
            .join(GroupWorkflow, GroupWorkflow.id == GroupWorkflowEvent.workflow_id)
            .join(Group, Group.id == GroupWorkflow.group_id)
            .outerjoin(
                ChatSession,
                (ChatSession.group_id == GroupWorkflow.group_id)
                & (ChatSession.session_type == "group")
                & (ChatSession.is_primary.is_(True))
                & (ChatSession.deleted_at.is_(None)),
            )
            .where(
                GroupWorkflowEvent.event_type == "decision_action",
                GroupWorkflowEvent.dispatch_state == "pending",
            )
            .order_by(GroupWorkflowEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        event, workflow, decision_maker_participant_id, session_id = row
        event.dispatch_state = "claimed"
        return event.id, workflow.tenant_id, decision_maker_participant_id, session_id, dict(event.payload or {})


async def dispatch_decision_actions_once() -> bool:
    from app.services.group_decision.wake import build_decision_wake_content

    claimed = await _claim_decision_action()
    if claimed is None:
        return False
    event_id, tenant_id, decision_maker_participant_id, session_id, payload = claimed
    if decision_maker_participant_id is None or session_id is None:
        await _settle(event_id, dispatched=True)
        return True
    try:
        async with async_session() as db, db.begin():
            maker = await db.scalar(
                select(Participant).where(Participant.id == decision_maker_participant_id)
            )
            if maker is None or maker.type != "agent":
                await _settle(event_id, dispatched=True)
                return True
            workflow = await db.scalar(
                select(GroupWorkflow)
                .join(GroupWorkflowEvent, GroupWorkflowEvent.workflow_id == GroupWorkflow.id)
                .where(GroupWorkflowEvent.id == event_id)
            )
            if workflow is None:
                await _settle(event_id, dispatched=True)
                return True
            await group_message_service.enqueue_group_message(
                db,
                tenant_id=tenant_id,
                group_id=workflow.group_id,
                session_id=session_id,
                sender_participant_id=decision_maker_participant_id,
                mention_participant_ids=[decision_maker_participant_id],
                message_id=uuid.uuid5(uuid.NAMESPACE_URL, f"group-workflow-decision:{event_id}"),
                content=build_decision_wake_content(payload),
            )
        await _settle(event_id, dispatched=True)
    except Exception:
        logger.exception("Group workflow decision action %s could not be dispatched", event_id)
        await _settle(event_id, dispatched=False)
    return True


async def start_group_workflow_worker(scan_seconds: float = 2.0) -> None:
    logger.info("Group workflow worker started")
    last_digest_scan = 0.0
    while True:
        processed = await dispatch_leader_actions_once()
        processed = await dispatch_decision_actions_once() or processed
        now = time.monotonic()
        if now - last_digest_scan >= _DIGEST_SCAN_SECONDS:
            try:
                await daily_digest.enqueue_daily_digests_once()
            except Exception:
                logger.exception("Group workflow daily digest scan failed")
            last_digest_scan = now
        await asyncio.sleep(0 if processed else scan_seconds)


__all__ = [
    "build_leader_wake_content",
    "dispatch_decision_actions_once",
    "dispatch_leader_actions_once",
    "start_group_workflow_worker",
]
