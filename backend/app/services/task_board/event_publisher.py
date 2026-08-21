"""TaskBoardEventPublisher: persists event + fires PG NOTIFY for Chief subscription."""
from __future__ import annotations
import uuid
import logging
from typing import Any
from app.database import async_session
from app.models.task_board_event import TaskBoardEvent

logger = logging.getLogger(__name__)


class TaskBoardEventPublisher:
    async def publish(
        self,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        task_card_id: uuid.UUID,
        event_type: str,
        actor_id: uuid.UUID,
        actor_type: str,
        payload: dict | None = None,
    ) -> None:
        event = TaskBoardEvent(
            tenant_id=tenant_id,
            group_id=group_id,
            task_card_id=task_card_id,
            event_type=event_type,
            payload=payload or {},
            actor_id=actor_id,
            actor_type=actor_type,
        )
        async with async_session() as db:
            db.add(event)
            await db.commit()
            await db.refresh(event)
        # Best-effort PG NOTIFY (non-fatal if it fails)
        try:
            from app.database import engine
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f"NOTIFY task_board_events, '{event.id}'")
        except Exception as exc:
            logger.warning("PG NOTIFY failed (event still persisted): %s", exc)
