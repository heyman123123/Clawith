"""Database-claimed worker loop for durable team provisioning jobs."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from app.database import async_session
from app.models.team_builder import TeamProvisionJob
from app.services.team_builder.provisioning import TeamProvisioningError, provision_job

logger = logging.getLogger(__name__)


async def _claim_job() -> uuid.UUID | None:
    async with async_session() as db, db.begin():
        result = await db.execute(
            select(TeamProvisionJob)
            .where(TeamProvisionJob.status.in_(("queued", "retryable_failed")))
            .order_by(TeamProvisionJob.updated_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None
        job.status = "validating"
        return job.id


async def process_one_job() -> bool:
    job_id = await _claim_job()
    if job_id is None:
        return False
    try:
        async with async_session() as db:
            await provision_job(db, job_id=job_id)
            await db.commit()
    except TeamProvisioningError as exc:
        logger.warning("Team provision job %s failed: %s", job_id, exc)
        async with async_session() as db:
            result = await db.execute(select(TeamProvisionJob).where(TeamProvisionJob.id == job_id).with_for_update())
            job = result.scalar_one_or_none()
            if job is not None and job.status != "completed":
                job.status = "retryable_failed" if exc.retryable else "failed"
                job.error_code = exc.code
                job.error_message = str(exc)
                await db.commit()
    except Exception:
        logger.exception("Team provision job %s crashed", job_id)
        async with async_session() as db:
            result = await db.execute(select(TeamProvisionJob).where(TeamProvisionJob.id == job_id).with_for_update())
            job = result.scalar_one_or_none()
            if job is not None and job.status != "completed":
                job.status = "retryable_failed"
                job.error_code = "team_provision_unexpected_error"
                job.error_message = "Team provisioning stopped unexpectedly"
                await db.commit()
    return True


async def start_team_provision_worker(scan_seconds: float = 2.0) -> None:
    logger.info("Team provision worker started")
    while True:
        processed = await process_one_job()
        await asyncio.sleep(0 if processed else scan_seconds)
