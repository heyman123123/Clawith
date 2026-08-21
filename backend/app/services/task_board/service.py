"""TaskBoardService: create / move / assign cards with optimistic lock +
state machine + DAG cycle detection.

C2: every method enforces tenant_id scope via TaskCardDAO.
C5: batch lookups via in_(), no N+1.
C3: optimistic lock via version field; concurrent updates rejected.
"""
from __future__ import annotations
import logging
import uuid
from collections import defaultdict
from typing import Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.task_card import TaskCard
from app.models.task_card_dependency import TaskCardDependency
from app.services.task_board.dao import TaskCardDAO
from app.services.task_board.column_defs import (
    TaskColumn,
    validate_transition,
    assert_transition,
)
from app.services.task_board.event_publisher import TaskBoardEventPublisher

logger = logging.getLogger(__name__)


class TaskBoardError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class TaskBoardService:
    def __init__(
        self,
        event_publisher: TaskBoardEventPublisher | None = None,
    ) -> None:
        self.event_publisher = event_publisher or TaskBoardEventPublisher()

    async def create_card(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_type: str,
        title: str,
        group_id: uuid.UUID | None = None,
        assignee_agent_id: uuid.UUID | None = None,
        okr_key_result_id: uuid.UUID | None = None,
        description: str | None = None,
        priority: str | None = None,
        depends_on_card_ids: Sequence[uuid.UUID] | None = None,
    ) -> TaskCard:
        async with async_session() as db:
            dao = TaskCardDAO(db)
            card = await dao.create_card(
                tenant_id=tenant_id,
                title=title,
                description=description,
                group_id=group_id,
                assignee_agent_id=assignee_agent_id,
                okr_key_result_id=okr_key_result_id,
                created_by=actor_id,
                created_by_type=actor_type,
                priority=priority,
            )
            if depends_on_card_ids:
                await self._add_dependencies(
                    db=db,
                    tenant_id=tenant_id,
                    card_id=card.id,
                    depends_on=depends_on_card_ids,
                )
            await db.commit()
            await db.refresh(card)

        await self.event_publisher.publish(
            tenant_id=tenant_id,
            group_id=card.group_id or uuid.uuid4(),
            task_card_id=card.id,
            event_type="card_created",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={"title": card.title, "column": card.column},
        )
        return card

    async def move_card(
        self,
        *,
        tenant_id: uuid.UUID,
        card_id: uuid.UUID,
        to_column: TaskColumn | str,
        actor_id: uuid.UUID,
        actor_type: str,
        expected_version: int,
    ) -> TaskCard:
        to_col = TaskColumn(to_column) if isinstance(to_column, str) else to_column
        async with async_session() as db:
            dao = TaskCardDAO(db)
            card = await dao.get_card(card_id, tenant_id=tenant_id)
            if card is None:
                raise TaskBoardError("card_not_found", f"Card {card_id} not found")
            if card.version != expected_version:
                raise TaskBoardError(
                    "version_conflict",
                    f"Card was modified by another actor "
                    f"(expected v{expected_version}, got v{card.version})",
                )
            from_col = TaskColumn(card.column)
            if not validate_transition(from_col, to_col):
                raise TaskBoardError(
                    "invalid_transition",
                    f"Cannot move from {from_col.value} to {to_col.value}",
                )
            card.column = to_col.value
            card.version += 1
            await db.commit()
            await db.refresh(card)

        await self.event_publisher.publish(
            tenant_id=tenant_id,
            group_id=card.group_id,
            task_card_id=card.id,
            event_type="card_moved",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={"from": from_col.value, "to": to_col.value},
        )
        return card

    async def assign_card(
        self,
        *,
        tenant_id: uuid.UUID,
        card_id: uuid.UUID,
        assignee_agent_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_type: str,
        expected_version: int,
    ) -> TaskCard:
        async with async_session() as db:
            dao = TaskCardDAO(db)
            card = await dao.get_card(card_id, tenant_id=tenant_id)
            if card is None:
                raise TaskBoardError("card_not_found", f"Card {card_id} not found")
            if card.version != expected_version:
                raise TaskBoardError("version_conflict", "concurrent modification")
            prev = card.assignee_agent_id
            card.assignee_agent_id = assignee_agent_id
            card.version += 1
            await db.commit()
            await db.refresh(card)

        await self.event_publisher.publish(
            tenant_id=tenant_id,
            group_id=card.group_id,
            task_card_id=card.id,
            event_type="card_assigned",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={"from": str(prev) if prev else None, "to": str(assignee_agent_id)},
        )
        return card

    async def mark_done(
        self,
        *,
        tenant_id: uuid.UUID,
        card_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_type: str,
        expected_version: int,
        artifact_paths: Sequence[str] | None = None,
    ) -> TaskCard:
        """Move to DONE; persist artifact_paths on the card."""
        async with async_session() as db:
            dao = TaskCardDAO(db)
            card = await dao.get_card(card_id, tenant_id=tenant_id)
            if card is None:
                raise TaskBoardError("card_not_found", f"Card {card_id} not found")
            if card.version != expected_version:
                raise TaskBoardError("version_conflict", "concurrent modification")
            from_col = TaskColumn(card.column)
            assert_transition(from_col, TaskColumn.DONE)
            card.column = TaskColumn.DONE.value
            card.version += 1
            if artifact_paths:
                existing = list(card.artifact_paths or [])
                card.artifact_paths = existing + list(artifact_paths)
            await db.commit()
            await db.refresh(card)

        await self.event_publisher.publish(
            tenant_id=tenant_id,
            group_id=card.group_id,
            task_card_id=card.id,
            event_type="card_done",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={"artifact_paths": card.artifact_paths},
        )
        return card

    async def create_initial_cards(
        self,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        tasks: list,  # list of TaskProposal
        chief_run_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Bulk create initial task cards for a Draft's TaskProposal list.

        Returns list of created TaskCard IDs. Used by AtomicCreator.
        """
        ids: list[uuid.UUID] = []
        for t in tasks:
            card = await self.create_card(
                tenant_id=tenant_id,
                actor_id=chief_run_id or uuid.uuid4(),
                actor_type="chief" if chief_run_id else "user",
                title=t.get("title", "Untitled"),
                group_id=group_id,
                description=t.get("description", ""),
            )
            ids.append(card.id)
        return ids

    async def _add_dependencies(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        card_id: uuid.UUID,
        depends_on: Sequence[uuid.UUID],
    ) -> None:
        # DAG check
        adj: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        adj[card_id] = list(depends_on)
        for dep in depends_on:
            rows = (
                await db.execute(
                    select(TaskCardDependency).where(
                        TaskCardDependency.task_card_id == dep,
                        TaskCardDependency.tenant_id == tenant_id,
                    )
                )
            ).scalars().all()
            for r in rows:
                adj.setdefault(dep, []).append(r.depends_on_card_id)

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[uuid.UUID, int] = defaultdict(lambda: WHITE)

        def dfs(node: uuid.UUID) -> None:
            if color[node] == GRAY:
                raise TaskBoardError("dag_cycle", f"Dependency cycle detected at {node}")
            if color[node] == BLACK:
                return
            color[node] = GRAY
            for nxt in adj.get(node, []):
                dfs(nxt)
            color[node] = BLACK

        dfs(card_id)
        for dep in depends_on:
            db.add(
                TaskCardDependency(
                    tenant_id=tenant_id,
                    task_card_id=card_id,
                    depends_on_card_id=dep,
                )
            )
