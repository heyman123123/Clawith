"""OrchestratorRunLoop: long-lived Chief Runtime.

Subscribes to events for one group, applies Reasoner + ActionExecutor per
event, updates chief_runs row to track lifecycle + failure count.

Deadlock avoidance (T-009 acceptance):
  5 consecutive Reasoner failures -> chief_runs.status = 'degraded',
  5-minute cooldown, then auto-retry. If still failing, mark 'stopped'
  and notify user + admin.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from app.database import async_session
from app.models.chief_run import ChiefRun
from app.services.agent_runtime.orchestrator.event_listener import EventListener
from app.services.agent_runtime.orchestrator.reasoner import Reasoner
from app.services.agent_runtime.orchestrator.action_executor import ActionExecutor
from app.services.agent_runtime.orchestrator.checkpoint_state import (
    GroupState,
    CHIEF_STATUS_DEGRADED,
    CHIEF_STATUS_STOPPED,
)

logger = logging.getLogger(__name__)


class OrchestratorRunLoop:
    FAILURE_THRESHOLD = 5
    COOLDOWN_SECONDS = 300  # 5 minutes

    def __init__(
        self,
        *,
        event_listener: EventListener,
        reasoner: Reasoner,
        action_executor: ActionExecutor,
    ) -> None:
        self.event_listener = event_listener
        self.reasoner = reasoner
        self.action_executor = action_executor

    async def run(self, *, group_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Main loop. Returns when chief_runs.status becomes 'stopped'."""
        chief_run = await self._load_chief_run(group_id, tenant_id)
        if chief_run is None:
            raise RuntimeError(f"ChiefRun for group {group_id} not found")

        group_state = GroupState(group_id=group_id)

        async for event in self.event_listener.subscribe(
            tenant_id=tenant_id, group_id=group_id
        ):
            if chief_run.status == CHIEF_STATUS_STOPPED:
                logger.info("ChiefRun %s stopped; exiting loop", chief_run.id)
                return

            try:
                action_plan = await self.reasoner.decide(
                    event=event, group_state=group_state
                )
                await self.action_executor.execute(
                    plan=action_plan,
                    group_id=group_id,
                    tenant_id=tenant_id,
                    chief_agent_id=chief_run.chief_agent_id,
                )
                await self._reset_failures(chief_run.id)
            except Exception as exc:
                logger.exception("Reasoner/Executor failed in Chief loop")
                await self._bump_failure(chief_run.id, str(exc))
                if chief_run.failure_count + 1 >= self.FAILURE_THRESHOLD:
                    await self._mark_degraded(chief_run.id, str(exc))
                    await asyncio.sleep(self.COOLDOWN_SECONDS)
                    chief_run = await self._load_chief_run(group_id, tenant_id)
                    if chief_run.status == CHIEF_STATUS_STOPPED:
                        return

    async def _load_chief_run(
        self, group_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ChiefRun | None:
        from sqlalchemy import select
        async with async_session() as db:
            row = (
                await db.execute(
                    select(ChiefRun).where(
                        ChiefRun.group_id == group_id,
                        ChiefRun.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            return row

    async def _bump_failure(self, chief_run_id: uuid.UUID, reason: str) -> None:
        async with async_session() as db:
            cr = await db.get(ChiefRun, chief_run_id)
            if cr is None:
                return
            cr.failure_count += 1
            cr.last_failure_reason = reason[:1000]
            await db.commit()

    async def _reset_failures(self, chief_run_id: uuid.UUID) -> None:
        async with async_session() as db:
            cr = await db.get(ChiefRun, chief_run_id)
            if cr is None:
                return
            if cr.failure_count > 0:
                cr.failure_count = 0
                cr.last_failure_reason = None
                if cr.status == CHIEF_STATUS_DEGRADED:
                    cr.status = "active"
                await db.commit()

    async def _mark_degraded(self, chief_run_id: uuid.UUID, reason: str) -> None:
        async with async_session() as db:
            cr = await db.get(ChiefRun, chief_run_id)
            if cr is None:
                return
            cr.status = CHIEF_STATUS_DEGRADED
            cr.last_failure_reason = reason[:1000]
            await db.commit()
        logger.warning(
            "ChiefRun %s marked degraded after %d failures: %s",
            chief_run_id,
            self.FAILURE_THRESHOLD,
            reason,
        )
