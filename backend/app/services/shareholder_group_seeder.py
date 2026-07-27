"""Idempotent shareholder governance group provisioning for a tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.governance import GovernanceRolePool
from app.models.group import Group, GroupMember
from app.models.project import ShareholderGroup
from app.models.user import User
from app.services import group_chat_service
from app.services.governance_seeder import seed_governance_role_pool_for_tenant
from app.services.participant_identity import get_or_create_agent_participant, get_or_create_user_participant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SHAREHOLDER_GROUP_TYPE = "shareholder"
SHAREHOLDER_GROUP_NAME = "股东群"
SHAREHOLDER_MEMBER_ROLE_KEYS: tuple[str, ...] = (
    "ceo",
    "cto",
    "coo",
    "cfo",
    "board_secretary",
)


async def _pool_agent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    role_key: str,
) -> Agent:
    pool_row = await db.scalar(
        select(GovernanceRolePool).where(
            GovernanceRolePool.tenant_id == tenant_id,
            GovernanceRolePool.role_key == role_key,
        )
    )
    if pool_row is None:
        raise RuntimeError(f"Governance role pool missing for {role_key!r}")
    agent = await db.get(Agent, pool_row.agent_id)
    if agent is None or agent.deleted_at is not None:
        raise RuntimeError(f"Governance agent missing for {role_key!r}")
    return agent


async def _ensure_group_members(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    participant_ids: list[uuid.UUID],
) -> None:
    existing_members = await db.execute(
        select(GroupMember.participant_id).where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
    )
    member_ids = set(existing_members.scalars().all())
    now = datetime.now(UTC)
    for participant_id in participant_ids:
        if participant_id in member_ids:
            continue
        existing_membership = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.participant_id == participant_id,
            )
        )
        if existing_membership is not None:
            existing_membership.removed_at = None
            existing_membership.joined_at = now
            continue
        db.add(
            GroupMember(
                id=uuid.uuid4(),
                group_id=group_id,
                participant_id=participant_id,
                role="member",
                joined_at=now,
            )
        )
    await db.flush()


async def ensure_shareholder_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
) -> Group:
    """Idempotent: ShareholderGroup row + members (human + CEO/CTO/COO/CFO + Board Secretary).

    Sets group.owner_agent_id to Board Secretary agent id.
    """
    await seed_governance_role_pool_for_tenant(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
    )

    board_secretary = await _pool_agent(db, tenant_id=tenant_id, role_key="board_secretary")
    governance_agents = [
        await _pool_agent(db, tenant_id=tenant_id, role_key=role_key)
        for role_key in SHAREHOLDER_MEMBER_ROLE_KEYS
    ]

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
    for agent in governance_agents:
        participant = await get_or_create_agent_participant(
            db,
            agent.id,
            display_name=agent.name,
            avatar_url=agent.avatar_url,
        )
        agent_participant_ids.append(participant.id)

    shareholder_row = await db.scalar(
        select(ShareholderGroup).where(ShareholderGroup.tenant_id == tenant_id)
    )

    if shareholder_row is None:
        group = await group_chat_service.create_group(
            db,
            tenant_id=tenant_id,
            creator_participant_id=creator_participant.id,
            name=SHAREHOLDER_GROUP_NAME,
            description=(
                "公司级项目进展、资源管控与跨项目决策群。常驻 CEO/CTO/COO/CFO 与 Board Secretary；"
                "Board Secretary 为群主，负责纪要、确认与决策下发协调。"
            ),
            member_participant_ids=agent_participant_ids,
        )
        group.group_type = SHAREHOLDER_GROUP_TYPE
        group.owner_agent_id = board_secretary.id
        await db.flush()
        shareholder_row = ShareholderGroup(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            group_id=group.id,
            creator_id=creator_id,
        )
        db.add(shareholder_row)
        await db.flush()
    else:
        group = await db.get(Group, shareholder_row.group_id)
        if group is None or group.deleted_at is not None:
            raise RuntimeError("Shareholder group record is inconsistent")
        group.group_type = SHAREHOLDER_GROUP_TYPE
        group.owner_agent_id = board_secretary.id
        await _ensure_group_members(
            db,
            group_id=group.id,
            participant_ids=[*agent_participant_ids, creator_participant.id],
        )

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
            title="公司治理",
        )

    return group
