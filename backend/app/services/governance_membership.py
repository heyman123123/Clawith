"""Select governance agents for a project decision group."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.governance import GovernanceRolePool
from app.models.participant import Participant

_DECISION_PRIORITY = ("ceo", "cto", "coo", "cfo", "cmo")
_REVIEW_PRIORITY = (
    "product_review",
    "tech_architecture",
    "legal_compliance",
    "data_ai",
    "finance_roi",
    "risk_dependency",
)


async def _ensure_agent_participant(
    db: AsyncSession,
    *,
    agent: Agent,
) -> Participant:
    participant = await db.scalar(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id == agent.id,
        )
    )
    if participant is not None:
        return participant
    participant = Participant(
        type="agent",
        ref_id=agent.id,
        display_name=agent.name,
        avatar_url=agent.avatar_url,
    )
    db.add(participant)
    await db.flush()
    return participant


def _select_role_keys(
    enabled_rows: list[GovernanceRolePool],
    *,
    role_type: str,
    priority: tuple[str, ...],
    limit: int,
) -> list[str]:
    enabled_by_key = {
        row.role_key: row
        for row in enabled_rows
        if row.role_type == role_type and row.is_default_enabled
    }
    selected: list[str] = []
    for role_key in priority:
        if role_key in enabled_by_key and role_key not in selected:
            selected.append(role_key)
        if len(selected) >= limit:
            break
    return selected


async def select_decision_group_members(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    leader_participant: Participant,
) -> list[Participant]:
    """Pick governance pool members for a decision group (plus the project leader)."""
    rows = list(
        (
            await db.execute(
                select(GovernanceRolePool, Agent)
                .join(Agent, Agent.id == GovernanceRolePool.agent_id)
                .where(
                    GovernanceRolePool.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                )
            )
        ).all()
    )
    pool_rows = [pool for pool, _agent in rows]
    agents_by_id = {agent.id: agent for _pool, agent in rows}

    decision_keys = _select_role_keys(pool_rows, role_type="decision", priority=_DECISION_PRIORITY, limit=2)
    review_keys = _select_role_keys(pool_rows, role_type="review", priority=_REVIEW_PRIORITY, limit=3)
    selected_keys = decision_keys + review_keys

    key_to_agent_id = {pool.role_key: pool.agent_id for pool in pool_rows}
    participants: list[Participant] = [leader_participant]
    seen_ids = {leader_participant.id}

    for role_key in selected_keys:
        agent_id = key_to_agent_id.get(role_key)
        if agent_id is None:
            continue
        agent = agents_by_id.get(agent_id)
        if agent is None:
            continue
        participant = await _ensure_agent_participant(db, agent=agent)
        if participant.id in seen_ids:
            continue
        participants.append(participant)
        seen_ids.add(participant.id)

    return participants
