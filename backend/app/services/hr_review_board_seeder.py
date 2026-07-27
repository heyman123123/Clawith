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
from app.services.storage import store_agent_bytes

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
    (
        "HR Secretary",
        "HR 秘书",
        "纪要与出卡：强制产出 ≥3 proposals JSON，并在群内发出方案卡片与确认回执。",
    ),
)

_PROPOSAL_JSON_SHAPE = """
```json
{
  "proposals": [
    {
      "id": "proposal_1",
      "label": "精简 MVP",
      "card_summary": "3 人小队：群主 PM + 前端 + 后端",
      "roles": [
        {
          "key": "pm",
          "name": "项目经理",
          "duties": "…",
          "soul": "完整 soul.md 正文…",
          "is_group_leader": true,
          "suggested_tools": ["group_write_workspace_file"],
          "suggested_permissions": {"scope_type": "company", "access_level": "use"}
        }
      ]
    }
  ]
}
```
""".strip()

_HR_PROTOCOL_COMMON = """
## HR Review Protocol
1. Capture the user's requirement and restate success criteria.
2. Debate with HR Org Designer and HR Strategist until roles are justified.
3. Produce **at least 3** distinct team proposals before any card is sent.
4. Every role must include non-empty `duties`, full `soul` markdown, `suggested_tools`, and `suggested_permissions`.
5. Each proposal must include `id`, `label`, and `card_summary`.
6. Exactly one role per proposal has `is_group_leader=true`.
""".strip()


def _hr_agent_soul(name: str, role_title: str, bio: str) -> str:
    header = f"# {name}\n\n{role_title} — {bio}\n"
    if name == "HR Secretary":
        body = f"""{_HR_PROTOCOL_COMMON}

## Secretary Output Protocol
You own minutes, structured proposals, card delivery, and confirmation receipts.

When the board converges, you MUST emit a fenced JSON block with **≥3 proposals** using this shape:

{_PROPOSAL_JSON_SHAPE}

Do not paraphrase proposals in prose instead of outputting valid JSON.
After the user selects a proposal, send a confirmation receipt with the execution group link.
"""
    elif name == "HR Recruiter":
        body = f"""{_HR_PROTOCOL_COMMON}

## Recruiter Focus
Lead Session A team-building discussions. Propose diverse execution teams tailored to the brief.
Coordinate with HR Secretary so proposals JSON is complete before cards go out.
"""
    elif name == "HR Org Designer":
        body = f"""{_HR_PROTOCOL_COMMON}

## Org Designer Focus
Assess governance coverage (decision + review roles). Flag when Session B top-up is needed.
Ensure each proposed role has clear duties, soul, tools, and permissions.
"""
    else:
        body = f"""{_HR_PROTOCOL_COMMON}

## Strategist Focus
Balance cross-requirement staffing and reuse of tenant governance agents.
Challenge over/under-staffing before proposals are finalized.
"""
    return f"{header}\n{body}\n"


async def _write_hr_agent_soul(agent: Agent, *, role_title: str, bio: str) -> None:
    await store_agent_bytes(
        agent.id,
        "soul.md",
        _hr_agent_soul(agent.name, role_title, bio).encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
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
    created = agent is None
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
    if created:
        from app.services.agent_manager import agent_manager

        await agent_manager.initialize_agent_files(db, agent)
    await _write_hr_agent_soul(agent, role_title=role_title, bio=bio)
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
        for participant_id in (*agent_participant_ids, creator_participant.id):
            if participant_id in member_ids:
                continue
            existing_membership = await db.scalar(
                select(GroupMember).where(
                    GroupMember.group_id == group.id,
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
