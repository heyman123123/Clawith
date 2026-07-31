"""Create, approve, reject, and report group-level decisions."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import ChatMessage
from app.models.group import Group, GroupMember
from app.models.group_decision import GroupDecisionRequest
from app.models.group_workflow import GroupWorkflowStage
from app.models.participant import Participant
from app.services import chat_session_service
from app.services.group_workflow import service as group_workflow_service

logger = logging.getLogger(__name__)

EXCEPTION_CATEGORIES = frozenset({"human_comms", "external_deploy", "finance", "uncertain"})
VALID_CATEGORIES = frozenset({"routine", *EXCEPTION_CATEGORIES})
_SENSITIVE_RE = re.compile(
    r"(预算|打款|付款|合同|财务|发票|对外部署|上线|客户沟通|对外沟通|采购|签约)",
    re.IGNORECASE,
)


class GroupDecisionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_category(category: str, *, title: str = "", summary: str = "") -> str:
    raw = (category or "").strip().lower()
    if raw not in VALID_CATEGORIES:
        return "uncertain"
    if raw == "routine" and _SENSITIVE_RE.search(f"{title}\n{summary}"):
        return "uncertain"
    return raw


async def resolve_report_recipients(db: AsyncSession, group: Group) -> list[uuid.UUID]:
    """null → all human managers; [] → none; list → explicit active members only."""
    configured = group.decision_report_participant_ids
    if configured is not None and len(configured) == 0:
        return []

    if configured is None:
        result = await db.execute(
            select(Participant.id)
            .join(GroupMember, GroupMember.participant_id == Participant.id)
            .where(
                GroupMember.group_id == group.id,
                GroupMember.removed_at.is_(None),
                GroupMember.role == "manager",
                Participant.type == "user",
            )
        )
        return list(result.scalars().all())

    wanted: list[uuid.UUID] = []
    for raw in configured:
        try:
            wanted.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid decision report recipient %r for group %s", raw, group.id)
    if not wanted:
        return []
    result = await db.execute(
        select(Participant.id)
        .join(GroupMember, GroupMember.participant_id == Participant.id)
        .where(
            GroupMember.group_id == group.id,
            GroupMember.removed_at.is_(None),
            Participant.id.in_(wanted),
            Participant.type == "user",
        )
    )
    return list(result.scalars().all())


async def _human_managers(db: AsyncSession, group_id: uuid.UUID) -> list[Participant]:
    result = await db.execute(
        select(Participant)
        .join(GroupMember, GroupMember.participant_id == Participant.id)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
            GroupMember.role == "manager",
            Participant.type == "user",
        )
    )
    return list(result.scalars().all())


async def _require_human_manager(
    db: AsyncSession, *, group_id: uuid.UUID, actor_participant_id: uuid.UUID
) -> Participant:
    actor = await db.scalar(select(Participant).where(Participant.id == actor_participant_id))
    if actor is None or actor.type != "user":
        raise GroupDecisionError("decision_approver_invalid", "Only a human manager can decide")
    membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.participant_id == actor_participant_id,
            GroupMember.removed_at.is_(None),
            GroupMember.role == "manager",
        )
    )
    if membership is None:
        raise GroupDecisionError("decision_approver_denied", "Actor is not an active group manager")
    return actor


async def _load_group(db: AsyncSession, group_id: uuid.UUID) -> Group:
    group = await db.scalar(select(Group).where(Group.id == group_id, Group.deleted_at.is_(None)))
    if group is None:
        raise GroupDecisionError("group_not_found", "Group was not found")
    return group


async def _stage_title(db: AsyncSession, stage_id: uuid.UUID | None) -> str:
    if stage_id is None:
        return "-"
    stage = await db.scalar(select(GroupWorkflowStage).where(GroupWorkflowStage.id == stage_id))
    return stage.title if stage is not None else "-"


def _report_text(
    *,
    title: str,
    conclusion: str,
    category: str,
    summary: str,
    stage_title: str,
    human_confirmed: bool,
) -> str:
    summary_one = (summary or "").strip().replace("\n", " ")
    if len(summary_one) > 160:
        summary_one = summary_one[:157] + "..."
    return (
        f"【决策汇报】{title}\n"
        f"结论：{conclusion}\n"
        f"类别：{category}\n"
        f"依据：{summary_one or '-'}\n"
        f"阶段：{stage_title}\n"
        f"人类确认：{'是' if human_confirmed else '否'}"
    )


async def _dm_user(
    db: AsyncSession,
    *,
    agent_participant_id: uuid.UUID,
    user_participant_id: uuid.UUID,
    content: str,
) -> None:
    agent_p = await db.scalar(select(Participant).where(Participant.id == agent_participant_id))
    user_p = await db.scalar(select(Participant).where(Participant.id == user_participant_id))
    if agent_p is None or agent_p.type != "agent" or user_p is None or user_p.type != "user":
        raise GroupDecisionError("decision_dm_invalid", "Decision maker DM requires agent→user participants")
    from app.models.agent import Agent

    agent = await db.scalar(select(Agent).where(Agent.id == agent_p.ref_id))
    if agent is None:
        raise GroupDecisionError("decision_dm_agent_missing", "Decision maker agent was not found")
    session = await chat_session_service.ensure_primary_direct_session(
        db,
        agent.tenant_id,
        agent.id,
        user_p.ref_id,
        user_p.id,
    )
    db.add(
        ChatMessage(
            id=uuid.uuid4(),
            agent_id=agent.id,
            user_id=user_p.ref_id,
            role="assistant",
            content=content,
            conversation_id=str(session.id),
            participant_id=agent_participant_id,
            mentions=[],
        )
    )
    session.last_message_at = _now()


async def send_decision_report(db: AsyncSession, decision: GroupDecisionRequest) -> None:
    if decision.report_sent_at is not None:
        return
    group = await _load_group(db, decision.group_id)
    recipients = await resolve_report_recipients(db, group)
    if not recipients:
        decision.report_sent_at = _now()
        await db.flush()
        return
    stage_title = await _stage_title(db, decision.stage_id)
    conclusion = {
        "auto_applied": "已自动拍板并生效",
        "approved": "已获人类确认并生效",
        "rejected": "未通过 / 已改方案",
    }.get(decision.status, decision.status)
    human_confirmed = decision.status in {"approved", "rejected"} and decision.approver_participant_id is not None
    text = _report_text(
        title=decision.title,
        conclusion=conclusion,
        category=decision.category,
        summary=decision.summary,
        stage_title=stage_title,
        human_confirmed=human_confirmed,
    )
    for recipient_id in recipients:
        try:
            await _dm_user(
                db,
                agent_participant_id=decision.decision_maker_participant_id,
                user_participant_id=recipient_id,
                content=text,
            )
        except Exception:
            logger.exception(
                "Failed to send decision report %s to recipient %s",
                decision.id,
                recipient_id,
            )
    decision.report_sent_at = _now()
    await db.flush()


async def _create_request(
    db: AsyncSession,
    *,
    group: Group,
    category: str,
    title: str,
    summary: str,
    recommendation: str | None,
    options_json: list | dict | None,
    workflow_id: uuid.UUID | None,
    stage_id: uuid.UUID | None,
    status: str,
) -> GroupDecisionRequest:
    if group.decision_maker_participant_id is None:
        raise GroupDecisionError("decision_maker_missing", "Group has no decision maker")
    decision = GroupDecisionRequest(
        id=uuid.uuid4(),
        tenant_id=group.tenant_id,
        group_id=group.id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        decision_maker_participant_id=group.decision_maker_participant_id,
        category=category,
        title=title.strip()[:300] or "项目决策",
        summary=(summary or "").strip(),
        recommendation=recommendation,
        options_json=options_json,
        status=status,
        decided_at=_now() if status != "pending_owner_confirm" else None,
    )
    db.add(decision)
    await db.flush()
    return decision


async def apply_routine_decision(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    title: str,
    summary: str,
    recommendation: str | None = None,
    workflow_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
    confirm_stage: bool = True,
) -> GroupDecisionRequest:
    group = await _load_group(db, group_id)
    category = normalize_category("routine", title=title, summary=summary)
    if category != "routine":
        return await request_owner_confirm(
            db,
            group_id=group_id,
            category=category,
            title=title,
            summary=summary,
            recommendation=recommendation,
            workflow_id=workflow_id,
            stage_id=stage_id,
        )
    decision = await _create_request(
        db,
        group=group,
        category="routine",
        title=title,
        summary=summary,
        recommendation=recommendation,
        options_json=None,
        workflow_id=workflow_id,
        stage_id=stage_id,
        status="auto_applied",
    )
    stage_outcome = "skipped"
    if confirm_stage and stage_id is not None:
        stage_outcome = await _apply_stage_after_decision(
            db,
            group=group,
            stage_id=stage_id,
        )
    try:
        await send_decision_report(db, decision)
    except Exception:
        logger.exception("Decision report failed for %s", decision.id)
    try:
        await group_workflow_service.notify_leader_decision_resolved(
            db,
            group_id=group.id,
            workflow_id=workflow_id or decision.workflow_id,
            stage_id=stage_id or decision.stage_id,
            title=decision.title,
            status=decision.status,
            summary=f"{decision.summary}\n[stage_outcome={stage_outcome}]",
            category=decision.category,
        )
    except Exception:
        logger.exception("Leader decision wake failed for %s", decision.id)
    return decision


async def _apply_stage_after_decision(
    db: AsyncSession,
    *,
    group: Group,
    stage_id: uuid.UUID,
) -> str:
    """Confirm awaiting stages; soft-handle stages that are not ready yet."""
    stage = await db.scalar(
        select(GroupWorkflowStage).where(GroupWorkflowStage.id == stage_id).with_for_update()
    )
    if stage is None:
        return "stage_missing"
    if stage.status == "awaiting_approval":
        await group_workflow_service.confirm_stage(
            db,
            stage_id=stage_id,
            actor_participant_id=group.decision_maker_participant_id,
            allow_decision_maker=True,
        )
        return "confirmed"
    if stage.status == "completed":
        return "already_completed"
    # Active/pending: decision is recorded, but evidence gate is not open yet.
    # Do not raise — the leader must finish evidence / reconcile first.
    logger.info(
        "Decision maker recorded routine decision but stage %s is %s (not awaiting_approval)",
        stage_id,
        stage.status,
    )
    return f"waiting_stage:{stage.status}"


async def request_owner_confirm(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    category: str,
    title: str,
    summary: str,
    recommendation: str | None = None,
    options_json: list | dict | None = None,
    workflow_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
) -> GroupDecisionRequest:
    group = await _load_group(db, group_id)
    normalized = normalize_category(category, title=title, summary=summary)
    if normalized == "routine":
        normalized = "uncertain"
    decision = await _create_request(
        db,
        group=group,
        category=normalized,
        title=title,
        summary=summary,
        recommendation=recommendation,
        options_json=options_json,
        workflow_id=workflow_id,
        stage_id=stage_id,
        status="pending_owner_confirm",
    )
    managers = await _human_managers(db, group.id)
    stage_title = await _stage_title(db, stage_id)
    ask = (
        f"【决策求批】{decision.title}\n"
        f"类别：{decision.category}\n"
        f"阶段：{stage_title}\n"
        f"说明：{(summary or '').strip() or '-'}\n"
        f"建议：{(recommendation or '').strip() or '-'}\n"
        f"决策ID：{decision.id}\n"
        "请任一位群管理员在群工作流/决策页批准或拒绝。任一确认即可。"
    )
    for manager in managers:
        try:
            await _dm_user(
                db,
                agent_participant_id=group.decision_maker_participant_id,
                user_participant_id=manager.id,
                content=ask,
            )
        except Exception:
            logger.exception("Failed to DM manager %s for decision %s", manager.id, decision.id)
    return decision


async def classify_and_act(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    category: str,
    title: str,
    summary: str,
    recommendation: str | None = None,
    workflow_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
) -> GroupDecisionRequest:
    normalized = normalize_category(category, title=title, summary=summary)
    if normalized == "routine":
        return await apply_routine_decision(
            db,
            group_id=group_id,
            title=title,
            summary=summary,
            recommendation=recommendation,
            workflow_id=workflow_id,
            stage_id=stage_id,
            confirm_stage=True,
        )
    return await request_owner_confirm(
        db,
        group_id=group_id,
        category=normalized,
        title=title,
        summary=summary,
        recommendation=recommendation,
        workflow_id=workflow_id,
        stage_id=stage_id,
    )


async def approve_decision(
    db: AsyncSession,
    *,
    decision_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    note: str | None = None,
) -> GroupDecisionRequest:
    decision = await db.scalar(
        select(GroupDecisionRequest).where(GroupDecisionRequest.id == decision_id).with_for_update()
    )
    if decision is None:
        raise GroupDecisionError("decision_not_found", "Decision request was not found")
    await _require_human_manager(db, group_id=decision.group_id, actor_participant_id=actor_participant_id)
    if decision.status != "pending_owner_confirm":
        raise GroupDecisionError("decision_not_pending", "Decision is not awaiting confirmation")
    decision.status = "approved"
    approval_note = (note or "").strip()
    if approval_note:
        decision.summary = f"{decision.summary}\n确认说明：{approval_note}".strip()
    decision.approver_participant_id = actor_participant_id
    decision.decided_at = _now()
    await db.flush()
    if decision.stage_id is not None:
        stage = await db.scalar(select(GroupWorkflowStage).where(GroupWorkflowStage.id == decision.stage_id))
        if stage is not None and stage.status == "awaiting_approval":
            await group_workflow_service.confirm_stage(
                db,
                stage_id=decision.stage_id,
                actor_participant_id=decision.decision_maker_participant_id,
                allow_decision_maker=True,
            )
    await send_decision_report(db, decision)
    await group_workflow_service.notify_leader_decision_resolved(
        db,
        group_id=decision.group_id,
        workflow_id=decision.workflow_id,
        stage_id=decision.stage_id,
        title=decision.title,
        status=decision.status,
        summary=decision.summary,
        category=decision.category,
    )
    return decision


async def reject_decision(
    db: AsyncSession,
    *,
    decision_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    reason: str | None = None,
) -> GroupDecisionRequest:
    decision = await db.scalar(
        select(GroupDecisionRequest).where(GroupDecisionRequest.id == decision_id).with_for_update()
    )
    if decision is None:
        raise GroupDecisionError("decision_not_found", "Decision request was not found")
    await _require_human_manager(db, group_id=decision.group_id, actor_participant_id=actor_participant_id)
    if decision.status != "pending_owner_confirm":
        raise GroupDecisionError("decision_not_pending", "Decision is not awaiting confirmation")
    note = (reason or "").strip()
    if note:
        decision.summary = f"{decision.summary}\n拒绝原因：{note}".strip()
    decision.status = "rejected"
    decision.approver_participant_id = actor_participant_id
    decision.decided_at = _now()
    await db.flush()
    await send_decision_report(db, decision)
    await group_workflow_service.notify_leader_decision_resolved(
        db,
        group_id=decision.group_id,
        workflow_id=decision.workflow_id,
        stage_id=decision.stage_id,
        title=decision.title,
        status=decision.status,
        summary=decision.summary,
        category=decision.category,
    )
    return decision


async def list_decisions(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    status: str | None = None,
) -> list[GroupDecisionRequest]:
    stmt = select(GroupDecisionRequest).where(GroupDecisionRequest.group_id == group_id)
    if status:
        stmt = stmt.where(GroupDecisionRequest.status == status)
    stmt = stmt.order_by(GroupDecisionRequest.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


def decision_to_dict(decision: GroupDecisionRequest) -> dict[str, Any]:
    return {
        "id": str(decision.id),
        "tenant_id": str(decision.tenant_id),
        "group_id": str(decision.group_id),
        "workflow_id": str(decision.workflow_id) if decision.workflow_id else None,
        "stage_id": str(decision.stage_id) if decision.stage_id else None,
        "decision_maker_participant_id": str(decision.decision_maker_participant_id),
        "category": decision.category,
        "title": decision.title,
        "summary": decision.summary,
        "recommendation": decision.recommendation,
        "options_json": decision.options_json,
        "status": decision.status,
        "approver_participant_id": str(decision.approver_participant_id)
        if decision.approver_participant_id
        else None,
        "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
        "report_sent_at": decision.report_sent_at.isoformat() if decision.report_sent_at else None,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
    }
