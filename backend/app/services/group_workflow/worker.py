"""Recover durable workflow actions by activating the group leader / decision maker."""

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


def _at_protocol_hint(*, participant_id: str, display_name: str) -> str:
    return (
        f"先调用 at 工具传入 participant_id={participant_id}，"
        f"再在公开回复正文写出 @{display_name}。"
        "禁止只写 @名字却不调用 at（会触发 invalid_group_at）。"
    )


def _confirm_hint(payload: dict[str, Any]) -> str:
    decision_maker = payload.get("decision_maker")
    if isinstance(decision_maker, dict):
        dm_id = str(decision_maker.get("participant_id") or "").strip()
        dm_name = str(decision_maker.get("display_name") or "决策者").strip() or "决策者"
        if dm_id:
            return (
                f"项目级确认由决策者「{dm_name}」负责，不要让人类或成员做项目拍板。"
                f"{_at_protocol_hint(participant_id=dm_id, display_name=dm_name)}"
            )
    targets = payload.get("confirm_targets") or []
    if not isinstance(targets, list) or not targets:
        return (
            "当前没有决策者绑定：请在群内公开说明确认项；"
            "如需 @ 管理员，必须先调用 at 工具再写可见 @ 名字。"
        )
    parts: list[str] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        name = str(target.get("display_name") or "").strip()
        pid = str(target.get("participant_id") or "").strip()
        if name and pid:
            parts.append(_at_protocol_hint(participant_id=pid, display_name=name))
    if not parts:
        return "如需人类确认，请先调用 at 工具再在正文写 @名字。"
    return "；".join(parts)


def build_leader_wake_content(payload: dict[str, Any]) -> str:
    """Human-readable wake text keyed by leader_action kind."""
    kind = str(payload.get("kind") or "state_changed")
    stage = str(payload.get("stage_title") or "当前阶段")
    item = payload.get("item_title")
    item_part = f"；关联工作项：{item}" if item else ""
    confirm = _confirm_hint(payload)
    decision_maker = payload.get("decision_maker") if isinstance(payload.get("decision_maker"), dict) else None

    if kind == "approval_required":
        if decision_maker:
            return (
                f"工作流推进指令（待决策者拍板）：阶段「{stage}」已齐备证据{item_part}。"
                f"{confirm} "
                "你继续催证据与执行编排；不要自行向人类征求项目级拍板，也不要把拍板推给成员。"
                "需要项目拍板时，先调用 at 再 @决策者。不要等待心跳或定时。"
            )
        return (
            f"工作流推进指令（需确认）：阶段「{stage}」已齐备证据，等待确认后才能进入下一阶段"
            f"{item_part}。{confirm} 不要等待心跳或定时；在确认前可继续催未完成证据。"
        )
    if kind == "member_progress":
        actor_name = str(payload.get("actor_display_name") or "成员").strip() or "成员"
        return (
            f"工作流推进指令（成员进度）：「{actor_name}」已提交阶段「{stage}」证据{item_part}。"
            "请立刻公开确认进度、分派下一步或催其余未完成项。"
            "若需项目级拍板，先调用 at 再 @决策者，不要让人类或成员做项目决策。"
            "不要等待心跳或定时。"
        )
    if kind == "decision_resolved":
        dtitle = str(payload.get("decision_title") or "项目决策").strip() or "项目决策"
        dstatus = str(payload.get("decision_status") or "").strip()
        dsummary = str(payload.get("decision_summary") or "").strip()
        summary_part = f"依据：{dsummary}。" if dsummary else ""
        return (
            f"工作流推进指令（决策已定稿）：「{dtitle}」结论={dstatus or '-'}；阶段「{stage}」。"
            f"{summary_part}"
            "请立刻按结论公开分派下一步或处理遗留项，禁止干等。"
        )
    if kind == "blocker":
        return (
            f"工作流推进指令（阻塞）：阶段「{stage}」存在阻塞{item_part}。"
            f"请立刻公开处理阻塞或重新分派；{confirm}"
        )
    if kind == "stage_activated":
        return (
            f"工作流推进指令（阶段激活）：「{stage}」已激活{item_part}。"
            "请立刻在群内公开分派下一步可执行工作，并对你负责的工作项调用 "
            "group_workflow_submit_evidence 提交证据以推进阶段；禁止等 admin/人类批准。"
            "项目级拍板交给决策者，不要让人类或成员做项目决策。"
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
    from app.models.group import Group
    from app.services.group_decision.seed import ensure_group_decision_maker_from_group
    from app.services.group_decision.wake import build_decision_wake_content

    claimed = await _claim_decision_action()
    if claimed is None:
        return False
    event_id, tenant_id, decision_maker_participant_id, session_id, payload = claimed
    try:
        async with async_session() as db, db.begin():
            workflow = await db.scalar(
                select(GroupWorkflow)
                .join(GroupWorkflowEvent, GroupWorkflowEvent.workflow_id == GroupWorkflow.id)
                .where(GroupWorkflowEvent.id == event_id)
            )
            if workflow is None:
                await _settle(event_id, dispatched=True)
                return True
            group = await db.scalar(select(Group).where(Group.id == workflow.group_id))
            if group is not None and (
                decision_maker_participant_id is None or group.decision_maker_participant_id is None
            ):
                await ensure_group_decision_maker_from_group(
                    db,
                    group=group,
                    goal=group.description or group.name,
                    require_ready=False,
                )
                await db.refresh(group)
                decision_maker_participant_id = group.decision_maker_participant_id
            if decision_maker_participant_id is None or session_id is None:
                logger.warning(
                    "Decision action %s skipped: dm=%s session=%s",
                    event_id,
                    decision_maker_participant_id,
                    session_id,
                )
                await _settle(event_id, dispatched=True)
                return True
            maker = await db.scalar(
                select(Participant).where(Participant.id == decision_maker_participant_id)
            )
            if maker is None or maker.type != "agent":
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
