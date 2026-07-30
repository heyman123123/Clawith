"""Same-person-same-day OKR collection outreach ledger helpers."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.okr import MemberDailyReport, OKRCollectionOutreach


async def already_submitted_report(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    member_type: str,
    member_id: uuid.UUID,
    report_date: date,
) -> bool:
    existing = await db.scalar(
        select(MemberDailyReport.id).where(
            MemberDailyReport.tenant_id == tenant_id,
            MemberDailyReport.member_type == member_type,
            MemberDailyReport.member_id == member_id,
            MemberDailyReport.report_date == report_date,
        )
    )
    return existing is not None


async def already_outreached(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    member_type: str,
    member_id: uuid.UUID,
    report_date: date,
) -> bool:
    existing = await db.scalar(
        select(OKRCollectionOutreach.id).where(
            OKRCollectionOutreach.tenant_id == tenant_id,
            OKRCollectionOutreach.member_type == member_type,
            OKRCollectionOutreach.member_id == member_id,
            OKRCollectionOutreach.report_date == report_date,
        )
    )
    return existing is not None


async def record_outreach(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    member_type: str,
    member_id: uuid.UUID,
    report_date: date,
    source: str,
    group_id: uuid.UUID | None = None,
) -> bool:
    """Insert outreach row; return False if a same-day row already exists."""
    stmt = (
        insert(OKRCollectionOutreach)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            member_type=member_type,
            member_id=member_id,
            report_date=report_date,
            source=source,
            group_id=group_id,
        )
        .on_conflict_do_nothing(
            constraint="uq_okr_collection_outreach_day",
        )
        .returning(OKRCollectionOutreach.id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


__all__ = [
    "already_outreached",
    "already_submitted_report",
    "record_outreach",
]
