"""Recover durable workflow actions by activating the group leader once."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from app.database import async_session
from app.models.chat_session import ChatSession
from app.models.group_workflow import GroupWorkflow, GroupWorkflowEvent
from app.models.participant import Participant
from app.services import group_message_service

logger = logging.getLogger(__name__)


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
            kind = str(payload.get("kind") or "state_changed")
            stage = str(payload.get("stage_title") or "当前阶段")
            item = payload.get("item_title")
            detail = f"；关联工作项：{item}" if item else ""
            await group_message_service.enqueue_group_message(
                db,
                tenant_id=tenant_id,
                group_id=workflow.group_id,
                session_id=session_id,
                sender_participant_id=leader_participant_id,
                mention_participant_ids=[leader_participant_id],
                message_id=uuid.uuid5(uuid.NAMESPACE_URL, f"group-workflow-action:{event_id}"),
                content=(
                    f"工作流推进指令（{kind}）：{stage}{detail}。"
                    "请依据当前工作流状态在群内公开分发下一步、处理阻塞或向管理员请求确认。"
                ),
            )
        await _settle(event_id, dispatched=True)
    except Exception:
        logger.exception("Group workflow leader action %s could not be dispatched", event_id)
        await _settle(event_id, dispatched=False)
    return True


async def start_group_workflow_worker(scan_seconds: float = 2.0) -> None:
    logger.info("Group workflow worker started")
    while True:
        processed = await dispatch_leader_actions_once()
        await asyncio.sleep(0 if processed else scan_seconds)


__all__ = ["dispatch_leader_actions_once", "start_group_workflow_worker"]
