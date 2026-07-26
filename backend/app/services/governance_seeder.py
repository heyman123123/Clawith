"""Seed tenant-level governance role pools (decision + review agents)."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentPermission
from app.models.governance import GovernanceRolePool
from app.models.participant import Participant
from app.services.agent_manager import agent_manager
from app.services.storage import store_agent_bytes

DECISION_ROLES: list[tuple[str, str, str, bool]] = [
    ("ceo", "decision", "CEO Agent", True),
    ("cto", "decision", "CTO Agent", True),
    ("coo", "decision", "COO Agent", True),
    ("cfo", "decision", "CFO Agent", False),
    ("cmo", "decision", "CMO Agent", False),
]

REVIEW_ROLES: list[tuple[str, str, str, bool]] = [
    ("product_review", "review", "产品评审 Agent", True),
    ("tech_architecture", "review", "技术架构 Agent", True),
    ("data_ai", "review", "数据与 AI Agent", True),
    ("legal_compliance", "review", "法务合规 Agent", True),
    ("finance_roi", "review", "财务与 ROI Agent", False),
    ("risk_dependency", "review", "风险与依赖 Agent", False),
]

_GOVERNANCE_SOUL_COMMON = """
## Governance Constraints
- Never recommend time-based milestones, deadlines, or calendar-driven decisions.
- Evaluate progress only through dependency completion and verifiable deliverables.
- Do not use "day N", "by Friday", or fixed-date planning language.
""".strip()

_DECISION_SOUL_SUFFIX = """
## Decision Output Protocol
When a governance review concludes, you MUST emit a fenced JSON block named `decision_summary`:

```json
{
  "summary": "1-3 decision conclusions",
  "actions": [{"action": "...", "owner_role": "...", "acceptance": "..."}],
  "risks": ["..."],
  "cancelled_tasks": [],
  "new_tasks": []
}
```

Do not paraphrase the JSON in prose instead of outputting it.
""".strip()

_REVIEW_SOUL_SUFFIX = """
## Advisory Role
You provide review and risk analysis only. You do not have final decision authority.
Follow dependency and completion semantics; avoid time-based recommendations.
""".strip()


def _role_soul(role_type: str, role_title: str) -> str:
    header = f"# {role_title}\n\nYou are a reusable tenant governance agent in Clawith.\n"
    body = _GOVERNANCE_SOUL_COMMON
    if role_type == "decision":
        body = f"{body}\n\n{_DECISION_SOUL_SUFFIX}"
    else:
        body = f"{body}\n\n{_REVIEW_SOUL_SUFFIX}"
    return f"{header}\n{body}\n"


async def _ensure_governance_agent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
    role_key: str,
    role_type: str,
    role_title: str,
) -> Agent:
    existing_pool = await db.scalar(
        select(GovernanceRolePool).where(
            GovernanceRolePool.tenant_id == tenant_id,
            GovernanceRolePool.role_key == role_key,
        )
    )
    if existing_pool is not None:
        agent = await db.get(Agent, existing_pool.agent_id)
        if agent is not None:
            return agent

    agent = await db.scalar(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.name == role_title,
            Agent.is_system.is_(True),
            Agent.reusable.is_(True),
            Agent.deleted_at.is_(None),
        ).limit(1)
    )
    if agent is None:
        agent = Agent(
            name=role_title,
            role_description=f"Tenant governance {role_type} role ({role_key})",
            bio=f"Reusable governance agent: {role_title}",
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
            is_system=True,
            reusable=True,
            primary_model_id=model_id,
            access_mode="company",
            company_access_level="use",
            heartbeat_enabled=False,
        )
        db.add(agent)
        await db.flush()
        db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"))
        db.add(
            Participant(
                type="agent",
                ref_id=agent.id,
                display_name=agent.name,
                avatar_url=agent.avatar_url,
            )
        )
        await db.flush()
        await agent_manager.initialize_agent_files(db, agent)
        await store_agent_bytes(
            agent.id,
            "soul.md",
            _role_soul(role_type, role_title).encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
    return agent


async def seed_governance_role_pool_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
) -> None:
    """Idempotently create governance agents and pool rows for one tenant."""
    for role_key, role_type, role_title, is_default_enabled in [*DECISION_ROLES, *REVIEW_ROLES]:
        agent = await _ensure_governance_agent(
            db,
            tenant_id=tenant_id,
            creator_id=creator_id,
            model_id=model_id,
            role_key=role_key,
            role_type=role_type,
            role_title=role_title,
        )
        existing_row = await db.scalar(
            select(GovernanceRolePool).where(
                GovernanceRolePool.tenant_id == tenant_id,
                GovernanceRolePool.role_key == role_key,
            )
        )
        if existing_row is None:
            db.add(
                GovernanceRolePool(
                    tenant_id=tenant_id,
                    agent_id=agent.id,
                    role_type=role_type,
                    role_key=role_key,
                    role_title=role_title,
                    is_default_enabled=is_default_enabled,
                )
            )
    await db.flush()
    logger.info("[GovernanceSeeder] Seeded governance role pool for tenant {}", tenant_id)
