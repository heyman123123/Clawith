"""Seed and bind a dedicated decision-maker Agent for a group."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentPermission
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.tenant import Tenant
from app.models.user import User
from app.services.agent_manager import agent_manager
from app.services.participant_identity import get_or_create_agent_participant

logger = logging.getLogger(__name__)

_PERSONALITY = (
    "你是本群的项目决策者（Decision Maker），与群主分离。"
    "你代表用户对项目级事项拍板：阶段确认、计划/优先级/阻塞。"
    "常规事项可直接确认并推进；涉及人沟通、对外部署、财务，或拿不准时，必须私聊人类群管理求批，任一确认即可。"
    "禁止自行执行对外部署、打款或代替人类对外沟通。"
    "每一次拍板优先调用 group_decision_classify_and_act（系统会自动发私聊汇报）；"
    "若需补发，可用已授权的 send_platform_message 向管理员私聊。"
    "拍板后在群内先调用 at 工具再 @群主，公开告知结论以便群主继续编排。"
    "被唤醒后立刻调用 group_decision_classify_and_act；不要把项目拍板推给人类或成员；不要绕过群主直接指挥成员。"
)
_BOUNDARIES = (
    "群主负责编排与执行，你不取代群主。成员应向群主汇报，由群主再找你拍板。"
    "例外类别：human_comms / external_deploy / finance；uncertain 一律升级。"
    "创建时已默认授予跨空间私聊管理员权限（allow_group_cross_space）。"
)


def _with_cross_space_grant(policy: dict | None) -> dict:
    merged = dict(policy or {})
    merged["allow_group_cross_space"] = True
    return merged


async def _ensure_user_permission(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    access_level: str = "use",
) -> None:
    existing = await db.scalar(
        select(AgentPermission).where(
            AgentPermission.agent_id == agent_id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id == user_id,
        )
    )
    if existing is not None:
        if access_level == "manage" and existing.access_level != "manage":
            existing.access_level = "manage"
        return
    db.add(
        AgentPermission(
            agent_id=agent_id,
            scope_type="user",
            scope_id=user_id,
            access_level=access_level,
        )
    )


async def ensure_decision_maker_grants(
    db: AsyncSession,
    *,
    agent: Agent,
    group: Group,
    creator: User | None = None,
) -> None:
    """Grant cross-space DM + explicit user permissions for report recipients."""
    agent.autonomy_policy = _with_cross_space_grant(agent.autonomy_policy)
    if creator is not None:
        await _ensure_user_permission(
            db, agent_id=agent.id, user_id=creator.id, access_level="manage"
        )
    elif group.created_by_participant_id is not None:
        creator_participant = await db.scalar(
            select(Participant).where(Participant.id == group.created_by_participant_id)
        )
        if creator_participant is not None and creator_participant.type == "user":
            await _ensure_user_permission(
                db,
                agent_id=agent.id,
                user_id=creator_participant.ref_id,
                access_level="manage",
            )

    # Default report recipients: human managers in the group.
    managers = list(
        (
            await db.execute(
                select(Participant)
                .join(GroupMember, GroupMember.participant_id == Participant.id)
                .where(
                    GroupMember.group_id == group.id,
                    GroupMember.removed_at.is_(None),
                    GroupMember.role == "manager",
                    Participant.type == "user",
                )
            )
        ).scalars().all()
    )
    for manager in managers:
        await _ensure_user_permission(
            db, agent_id=agent.id, user_id=manager.ref_id, access_level="use"
        )

    # Explicit report recipients (when configured).
    configured = group.decision_report_participant_ids
    if isinstance(configured, list):
        for raw in configured:
            try:
                pid = uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
            participant = await db.scalar(select(Participant).where(Participant.id == pid))
            if participant is None or participant.type != "user":
                continue
            await _ensure_user_permission(
                db, agent_id=agent.id, user_id=participant.ref_id, access_level="use"
            )
    await db.flush()


async def ensure_group_decision_maker(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group: Group,
    creator: User,
    goal: str | None = None,
    require_ready: bool = True,
) -> uuid.UUID:
    """Idempotently ensure the group has a decision-maker agent participant."""
    if group.decision_maker_participant_id is not None:
        existing = await db.scalar(
            select(Participant).where(Participant.id == group.decision_maker_participant_id)
        )
        if existing is not None and existing.type == "agent":
            agent = await db.scalar(select(Agent).where(Agent.id == existing.ref_id))
            if agent is not None:
                await ensure_decision_maker_grants(
                    db, agent=agent, group=group, creator=creator
                )
            await _ensure_membership(db, group_id=group.id, participant_id=existing.id)
            return existing.id

    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise RuntimeError("tenant_not_found")

    agent = Agent(
        name="决策者",
        role_description="项目决策者：常规拍板，例外升级人类管理员，事后私聊汇报",
        bio=(goal or group.description or group.name or "项目决策")[:500],
        creator_id=creator.id,
        tenant_id=tenant_id,
        agent_type="native",
        primary_model_id=tenant.default_model_id,
        status="creating",
        access_mode="company",
        company_access_level="use",
        autonomy_policy=_with_cross_space_grant(None),
        max_llm_calls_per_day=tenant.default_max_llm_calls_per_day or 1000,
        max_triggers=tenant.default_max_triggers or 20,
        min_poll_interval_min=tenant.min_poll_interval_floor or 5,
        webhook_rate_limit=tenant.max_webhook_rate_ceiling or 5,
        heartbeat_interval_minutes=max(240, tenant.min_heartbeat_interval_minutes or 0),
    )
    db.add(agent)
    await db.flush()
    db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"))
    await ensure_decision_maker_grants(db, agent=agent, group=group, creator=creator)
    participant = await get_or_create_agent_participant(db, agent.id, agent.name, agent.avatar_url)
    await db.flush()

    boundaries = _BOUNDARIES
    if goal:
        boundaries = f"{_BOUNDARIES}\n团队目标：{goal}"
    try:
        await agent_manager.initialize_agent_files(
            db, agent, personality=_PERSONALITY, boundaries=boundaries
        )
        await agent_manager.start_container(db, agent)
    except Exception:
        agent.status = "error"
        logger.exception("Failed to initialize decision maker agent for group %s", group.id)
        if require_ready:
            raise

    group.decision_maker_participant_id = participant.id
    await _ensure_membership(db, group_id=group.id, participant_id=participant.id)
    await db.flush()
    return participant.id


async def ensure_group_decision_maker_from_group(
    db: AsyncSession,
    *,
    group: Group,
    goal: str | None = None,
    require_ready: bool = False,
) -> uuid.UUID | None:
    """Resolve creator from the group and ensure a decision maker when possible."""
    if group.decision_maker_participant_id is not None:
        await _ensure_membership(
            db, group_id=group.id, participant_id=group.decision_maker_participant_id
        )
        existing = await db.scalar(
            select(Participant).where(Participant.id == group.decision_maker_participant_id)
        )
        if existing is not None and existing.type == "agent":
            agent = await db.scalar(select(Agent).where(Agent.id == existing.ref_id))
            if agent is not None:
                await ensure_decision_maker_grants(db, agent=agent, group=group)
        return group.decision_maker_participant_id
    creator_participant = await db.scalar(
        select(Participant).where(Participant.id == group.created_by_participant_id)
    )
    if creator_participant is None or creator_participant.type != "user":
        return None
    creator = await db.scalar(select(User).where(User.id == creator_participant.ref_id))
    if creator is None:
        return None
    try:
        return await ensure_group_decision_maker(
            db,
            tenant_id=group.tenant_id,
            group=group,
            creator=creator,
            goal=goal,
            require_ready=require_ready,
        )
    except Exception:
        logger.exception("Lazy decision-maker seed failed for group %s", group.id)
        return None


async def _ensure_membership(
    db: AsyncSession, *, group_id: uuid.UUID, participant_id: uuid.UUID
) -> None:
    membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.participant_id == participant_id,
            GroupMember.removed_at.is_(None),
        )
    )
    if membership is not None:
        return
    db.add(
        GroupMember(
            id=uuid.uuid4(),
            group_id=group_id,
            participant_id=participant_id,
            role="member",
            joined_at=datetime.now(UTC),
            removed_at=None,
            session_read_state={},
        )
    )
    await db.flush()


async def rebind_decision_maker(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group: Group,
    decision_maker_participant_id: uuid.UUID,
) -> Group:
    participant = await db.scalar(
        select(Participant).where(Participant.id == decision_maker_participant_id)
    )
    if participant is None or participant.type != "agent":
        raise ValueError("decision_maker_invalid")
    agent = await db.scalar(
        select(Agent).where(Agent.id == participant.ref_id, Agent.tenant_id == tenant_id)
    )
    if agent is None:
        raise ValueError("decision_maker_invalid")
    await _ensure_membership(db, group_id=group.id, participant_id=participant.id)
    group.decision_maker_participant_id = participant.id
    await ensure_decision_maker_grants(db, agent=agent, group=group)
    await db.flush()
    return group
