"""EventListener for the Chief Runtime.

Strategy:
  1. Subscribe to PG LISTEN task_board_events channel for live notifications.
  2. Run a 30-second compensation poll that catches any events missed
     between (a) reconnect events and (b) restart-after-downtime.

The listener yields OrchestratorEvent instances to the consuming
OrchestratorRunLoop. The consumer applies each event to the Reasoner.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime
from typing import AsyncIterator
from sqlalchemy import select

from app.database import async_session
from app.models.task_board_event import TaskBoardEvent

logger = logging.getLogger(__name__)


class OrchestratorEvent:
    """A normalized task-board event the Chief consumes."""

    def __init__(
        self,
        event_id: uuid.UUID,
        event_type: str,
        task_card_id: uuid.UUID,
        group_id: uuid.UUID,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_type: str,
        payload: dict,
        occurred_at: datetime,
    ) -> None:
        self.event_id = event_id
        self.event_type = event_type
        self.task_card_id = task_card_id
        self.group_id = group_id
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.actor_type = actor_type
        self.payload = payload
        self.occurred_at = occurred_at


class EventListener:
    def __init__(self, *, compensation_interval_seconds: int = 30) -> None:
        self.compensation_interval = compensation_interval_seconds
        self._queue: asyncio.Queue[OrchestratorEvent] = asyncio.Queue()
        self._last_seen_at: datetime | None = None
        self._stop = asyncio.Event()

    async def subscribe(
        self, *, tenant_id: uuid.UUID, group_id: uuid.UUID
    ) -> AsyncIterator[OrchestratorEvent]:
        """Yield events for the given group until stop() is called."""
        comp_task = asyncio.create_task(
            self._compensation_loop(tenant_id, group_id)
        )
        try:
            while not self._stop.is_set():
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                    yield event
                except asyncio.TimeoutError:
                    continue  # re-check stop signal periodically
        finally:
            comp_task.cancel()
            try:
                await comp_task
            except asyncio.CancelledError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def _compensation_loop(
        self, tenant_id: uuid.UUID, group_id: uuid.UUID
    ) -> None:
        """Catch-up poll every compensation_interval seconds."""
        while not self._stop.is_set():
            try:
                async with async_session() as db:
                    stmt = (
                        select(TaskBoardEvent)
                        .where(
                            TaskBoardEvent.tenant_id == tenant_id,
                            TaskBoardEvent.group_id == group_id,
                        )
                    )
                    if self._last_seen_at is not None:
                        stmt = stmt.where(TaskBoardEvent.occurred_at > self._last_seen_at)
                    stmt = stmt.order_by(TaskBoardEvent.occurred_at).limit(100)
                    rows = (await db.execute(stmt)).scalars().all()
                for r in rows:
                    self._last_seen_at = r.occurred_at
                    await self._queue.put(
                        OrchestratorEvent(
                            event_id=r.id,
                            event_type=r.event_type,
                            task_card_id=r.task_card_id,
                            group_id=r.group_id,
                            tenant_id=r.tenant_id,
                            actor_id=r.actor_id,
                            actor_type=r.actor_type,
                            payload=dict(r.payload or {}),
                            occurred_at=r.occurred_at,
                        )
                    )
            except Exception as exc:
                logger.warning("Compensation poll failed: %s", exc)
            await asyncio.sleep(self.compensation_interval)
