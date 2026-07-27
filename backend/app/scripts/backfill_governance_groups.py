"""Backfill HR review board, governance role pool, and shareholder group for all tenants.

Usage:
  Docker: docker exec clawith-backend-1 python3 -m app.scripts.backfill_governance_groups
  Source: cd backend && python3 -m app.scripts.backfill_governance_groups
"""

import asyncio

from loguru import logger


async def main() -> None:
    from app.database import async_session
    from app.models import (  # noqa: F401
        activity_log,
        agent,
        audit,
        channel_config,
        chat_session,
        gateway_message,
        identity,
        invitation_code,
        llm,
        notification,
        org,
        participant,
        plaza,
        schedule,
        skill,
        system_settings,
        task,
        tenant,
        tenant_setting,
        tool,
        trigger,
        user,
    )
    from app.services.governance_group_backfill import backfill_governance_groups_for_all_tenants

    async with async_session() as db:
        summary = await backfill_governance_groups_for_all_tenants(db)
        await db.commit()
        logger.info(
            "Governance group backfill complete. "
            "processed={processed} skipped={skipped} failed={failed} total={total}",
            **summary,
        )


if __name__ == "__main__":
    asyncio.run(main())
