"""Terminal Runtime handler: enqueue group Run failure recovery for the leader."""

from __future__ import annotations

import logging

from app.services.agent_runtime.command_worker import CheckpointObservation, RuntimeRunRecord, RuntimeSessionFactory
from app.services.group_run_resume.service import ensure_resume_job_for_failed_run, error_from_lifecycle

logger = logging.getLogger(__name__)


class GroupRunFailureRecoveryHandler:
    """On failed group-session Runs, notify the leader (and schedule model probes)."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        lifecycle = checkpoint.state.get("lifecycle")
        if not isinstance(lifecycle, dict):
            return
        if lifecycle.get("status") != "failed":
            return
        if not run.session_id:
            return
        error_code, error_summary = error_from_lifecycle(lifecycle)
        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await ensure_resume_job_for_failed_run(
                        db,
                        tenant_id=run.tenant_id,
                        run_id=run.run_id,
                        session_id=run.session_id,
                        agent_id=run.agent_id,
                        error_code=error_code,
                        error_summary=error_summary,
                    )
        except Exception:
            logger.exception(
                "Group run failure recovery failed for run=%s session=%s",
                run.run_id,
                run.session_id,
            )


__all__ = ["GroupRunFailureRecoveryHandler"]
