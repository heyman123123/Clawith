"""Idempotent HR review board provisioning for a tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.agent import Agent, AgentPermission
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.user import User
from app.services import group_chat_service
from app.services.participant_identity import get_or_create_agent_participant, get_or_create_user_participant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

HR_REVIEW_BOARD_GROUP_TYPE = "hr_review_board"
HR_REVIEW_BOARD_GROUP_NAME = "HR 评审群"

HR_AGENT_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "HR Recruiter",
        "招聘专员",
        "主导 Session A（组建项目执行团队），根据项目需求提出多套团队方案。",
    ),
    (
        "HR Org Designer",
        "组织发展顾问",
        "主导 Session B（治理角色补全），评估决策团与智囊团覆盖度。",
    ),
    (
        "HR Strategist",
        "战略规划师",
        "跨项目长周期视角，平衡执行团队与治理角色资源。",
    ),
)


async def _get_or_create_hr_agent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
    name: str,
    role_title: str,
    bio: str,
) -> Agent:
    result = await db.execute(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.name == name,
            Agent.is_system.is_(True),
            Agent.deleted_at.is_(None),
        ).limit(1)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        agent = Agent(
            id=uuid.uuid4(),
            name=name,
            role_description=role_title,
            bio=bio,
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
            is_system=True,
            heartbeat_enabled=False,
            primary_model_id=model_id,
        )
        db.add_all((
            agent,
            AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"),
        ))
    elif model_id is not None:
        agent.primary_model_id = model_id
    await db.flush()
    return agent


async def ensure_hr_review_board(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
) -> Group:
    """Ensure tenant HR review board group, agents, and primary session exist."""
    hr_agents: list[Agent] = []
    for name, role_title, bio in HR_AGENT_SPECS:
        hr_agents.append(
            await _get_or_create_hr_agent(
                db,
                tenant_id=tenant_id,
                creator_id=creator_id,
                model_id=model_id,
                name=name,
                role_title=role_title,
                bio=bio,
            )
        )

    result = await db.execute(
        select(Group).where(
            Group.tenant_id == tenant_id,
            Group.group_type == HR_REVIEW_BOARD_GROUP_TYPE,
            Group.deleted_at.is_(None),
        ).limit(1)
    )
    group = result.scalar_one_or_none()
    creator = await db.get(User, creator_id)
    creator_display = creator.display_name if creator is not None else "Tenant Admin"
    creator_avatar = creator.avatar_url if creator is not None else None
    creator_participant = await get_or_create_user_participant(
        db,
        creator_id,
        creator_display,
        creator_avatar,
    )
    agent_participant_ids: list[uuid.UUID] = []
    for agent in hr_agents:
        participant = await get_or_create_agent_participant(
            db,
            agent.id,
            display_name=agent.name,
            avatar_url=agent.avatar_url,
        )
        agent_participant_ids.append(participant.id)

    if group is None:
        group = await group_chat_service.create_group(
            db,
            tenant_id=tenant_id,
            creator_participant_id=creator_participant.id,
            name=HR_REVIEW_BOARD_GROUP_NAME,
            description="租户级 HR 评审群：组建执行团队与治理角色补全。",
            member_participant_ids=agent_participant_ids,
        )
        group.group_type = HR_REVIEW_BOARD_GROUP_TYPE
        await db.flush()
    else:
        existing_members = await db.execute(
            select(GroupMember.participant_id).where(
                GroupMember.group_id == group.id,
                GroupMember.removed_at.is_(None),
            )
        )
        member_ids = set(existing_members.scalars().all())
        now = datetime.now(UTC)
        for participant_id in agent_participant_ids:
            if participant_id in member_ids:
                continue
            db.add(
                GroupMember(
                    id=uuid.uuid4(),
                    group_id=group.id,
                    participant_id=participant_id,
                    role="member",
                    joined_at=now,
                )
            )
        await db.flush()

    primary_session = await db.scalar(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.group_id == group.id,
            ChatSession.session_type == "group",
            ChatSession.is_primary.is_(True),
            ChatSession.deleted_at.is_(None),
        ).limit(1)
    )
    if primary_session is None:
        await group_chat_service.create_group_session(
            db,
            tenant_id=tenant_id,
            group_id=group.id,
            actor_participant_id=creator_participant.id,
            title="HR 评审",
        )

    return group
