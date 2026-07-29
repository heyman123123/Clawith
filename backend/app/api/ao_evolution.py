"""Role growth / evolution HTTP surface (需求 §4.2–4.4 / §8.4).

Exposes version history and one-step rollback so the Role Growth Centre
can operate without going through the chat tool loop.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.evolution import AgentRoleVersion
from app.models.user import User
from app.services.ao import evolution_engine
from app.services.security_shell import assert_tenant_owns

router = APIRouter(prefix="/ao", tags=["ao-evolution"])


def _tenant_id(user: User) -> uuid.UUID:
    tid = getattr(user, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant required")
    return tid


class RollbackIn(BaseModel):
    rationale: str = Field(default="manual rollback from growth centre", max_length=2000)


@router.get("/evolution/agents")
async def list_evolved_agents(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List tenant agents that already have at least one role version."""
    tenant_id = _tenant_id(current_user)
    current_versions = (
        await db.execute(
            select(AgentRoleVersion, Agent)
            .join(Agent, Agent.id == AgentRoleVersion.agent_id)
            .where(
                Agent.tenant_id == tenant_id,
                AgentRoleVersion.is_current.is_(True),
            )
            .order_by(AgentRoleVersion.created_at.desc())
            .limit(limit)
        )
    ).all()

    items = []
    for version, agent in current_versions:
        items.append(
            {
                "agent_id": str(agent.id),
                "name": agent.name,
                "role_description": agent.role_description or "",
                "current_version_no": version.version_no,
                "current_source": version.source,
                "quality_score": version.quality_score,
                "summary": version.summary,
            }
        )
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/agents/{agent_id}/versions")
async def list_agent_versions(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = _tenant_id(current_user)
    agent = await db.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    assert_tenant_owns(
        actor_tenant_id=str(tenant_id),
        record_tenant_id=str(agent.tenant_id),
        context="agent role versions",
    )

    history = await evolution_engine.get_version_history(db, agent_id=agent.id)
    current = await evolution_engine.get_current_version(db, agent_id=agent.id)
    return {
        "ok": True,
        "agent_id": str(agent.id),
        "name": agent.name,
        "role_description": agent.role_description or "",
        "current_version_id": str(current.id) if current else None,
        "versions": [
            {
                "id": str(v.id),
                "version_no": v.version_no,
                "source": v.source,
                "quality_score": v.quality_score,
                "summary": v.summary,
                "soul_md_preview": (v.soul_md or "")[:400],
                "is_current": bool(current and current.id == v.id),
            }
            for v in history
        ],
    }


@router.post("/agents/{agent_id}/rollback")
async def rollback_agent_one_step(
    agent_id: uuid.UUID,
    body: RollbackIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = _tenant_id(current_user)
    agent = await db.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    assert_tenant_owns(
        actor_tenant_id=str(tenant_id),
        record_tenant_id=str(agent.tenant_id),
        context="agent role rollback",
    )

    outcome = await evolution_engine.rollback_role_one_step(
        db,
        agent=agent,
        rationale=body.rationale,
        trigger_source="manual_rollback",
        actor_user_id=getattr(current_user, "id", None),
    )
    await db.commit()
    if not outcome.evolved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="nothing to roll back (need ≥2 versions)",
        )
    return {
        "ok": True,
        "agent_id": str(agent.id),
        "new_version_id": str(outcome.new_version_id) if outcome.new_version_id else None,
        "prior_version_id": str(outcome.prior_version_id) if outcome.prior_version_id else None,
        "record_id": str(outcome.record_id) if outcome.record_id else None,
        "evolved": outcome.evolved,
    }
