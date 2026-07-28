"""Backfill Agent.primary_model_id from Tenant.default_model_id for unassigned agents."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.llm import LLMModel
from app.models.tenant import Tenant


async def _tenant_default_model_is_usable(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> bool:
    """Return True iff the tenant's default_model_id points at an enabled, non-deleted model."""
    default_id = await db.scalar(select(Tenant.default_model_id).where(Tenant.id == tenant_id))
    if default_id is None:
        return False
    model = await db.scalar(
        select(LLMModel).where(
            LLMModel.id == default_id,
            LLMModel.deleted_at.is_(None),
            LLMModel.enabled.is_(True),
        ),
    )
    return model is not None


async def propagate_tenant_default_to_unassigned_agents(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> int:
    """Backfill Agent.primary_model_id = Tenant.default_model_id for agents with primary_model_id IS NULL.

    Returns the number of agents actually updated. Returns 0 (without writing) when the
    tenant has no default, or the default model is deleted/disabled, so callers can
    treat "nothing to do" as a normal outcome.
    """
    default_id = await db.scalar(select(Tenant.default_model_id).where(Tenant.id == tenant_id))
    if default_id is None:
        return 0
    if not await _tenant_default_model_is_usable(db, tenant_id):
        logger.info(
            "[default_propagation] tenant={} default_model_id={} is not usable — skipping backfill",
            tenant_id,
            default_id,
        )
        return 0

    result = await db.execute(
        update(Agent)
        .where(
            Agent.tenant_id == tenant_id,
            Agent.primary_model_id.is_(None),
            Agent.deleted_at.is_(None),
        )
        .values(primary_model_id=default_id)
    )
    count = int(result.rowcount or 0)
    if count:
        logger.info(
            "[default_propagation] backfilled tenant={} agents={count} -> default_model_id={default_id}",
            tenant_id,
            count=count,
            default_id=default_id,
        )
    return count


async def propagate_tenant_default_all_tenants(db: AsyncSession) -> dict[str, int]:
    """Run propagate_tenant_default_to_unassigned_agents across every active tenant.

    Returns a mapping of tenant_id (str) → backfilled agent count.
    """
    tenant_ids = list(
        (
            await db.scalars(
                select(Tenant.id).where(Tenant.is_active.is_(True)).order_by(Tenant.created_at)
            )
        ).all(),
    )
    summary: dict[str, int] = {}
    for tid in tenant_ids:
        summary[str(tid)] = await propagate_tenant_default_to_unassigned_agents(db, tid)
    return summary


__all__ = [
    "propagate_tenant_default_all_tenants",
    "propagate_tenant_default_to_unassigned_agents",
]