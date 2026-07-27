"""Idempotent backfill of HR review board, governance pool, and shareholder group."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from app.models.tenant import Tenant
from app.models.user import User
from app.services.governance_seeder import seed_governance_role_pool_for_tenant
from app.services.hr_review_board_seeder import ensure_hr_review_board
from app.services.shareholder_group_seeder import ensure_shareholder_group

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _resolve_tenant_creator_id(db: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID | None:
    org_admin = await db.scalar(
        select(User)
        .where(
            User.tenant_id == tenant_id,
            User.role == "org_admin",
            User.is_active.is_(True),
        )
        .order_by(User.created_at)
        .limit(1)
    )
    if org_admin is not None:
        return org_admin.id

    fallback = await db.scalar(
        select(User)
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
        .order_by(User.created_at)
        .limit(1)
    )
    return fallback.id if fallback is not None else None


async def ensure_governance_groups_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
) -> None:
    """Ensure HR board, governance role pool, and shareholder group for one tenant.

    Safe to call on registration, company create, join, and startup backfill.
    """
    await ensure_hr_review_board(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
    )
    await seed_governance_role_pool_for_tenant(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
    )
    await ensure_shareholder_group(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
    )


async def try_ensure_governance_groups_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
    context: str,
) -> bool:
    """Best-effort wrapper that logs failures without aborting the caller."""
    try:
        await ensure_governance_groups_for_tenant(
            db,
            tenant_id=tenant_id,
            creator_id=creator_id,
            model_id=model_id,
        )
        return True
    except Exception as exc:
        logger.exception(
            "[Governance] {} failed for tenant {} creator {}: {}",
            context,
            tenant_id,
            creator_id,
            exc,
        )
        return False


async def backfill_governance_groups_for_all_tenants(db: AsyncSession) -> dict[str, int]:
    """Backfill governance groups for every active tenant. Returns summary counts."""
    tenants = (
        await db.scalars(
            select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.created_at)
        )
    ).all()
    processed = 0
    failed = 0
    skipped = 0
    for tenant in tenants:
        creator_id = await _resolve_tenant_creator_id(db, tenant.id)
        if creator_id is None:
            skipped += 1
            logger.warning(
                "[Governance] skip tenant {} — no active user to own seeded groups",
                tenant.id,
            )
            continue
        success = await try_ensure_governance_groups_for_tenant(
            db,
            tenant_id=tenant.id,
            creator_id=creator_id,
            model_id=tenant.default_model_id,
            context="startup/backfill",
        )
        if success:
            processed += 1
        else:
            failed += 1
    return {
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "total": len(tenants),
    }
