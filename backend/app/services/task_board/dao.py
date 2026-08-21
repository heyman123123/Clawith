"""TaskCard DAO with strict tenant_id scoping (C2) and batch loading (C5).

Every query method REQUIRES tenant_id as a keyword-only argument and
includes it in the WHERE clause. There is no method that operates without
tenant scoping — callers cannot accidentally bypass the tenant boundary.

For batch operations, prefer list_cards_by_group / list_cards_by_assignee
which return lists in a single query (no N+1) and ordered by column then
position for stable Kanban rendering.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_card import TaskCard


class TaskCardDAO:
    """Async DAO for TaskCard with mandatory tenant scope."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_card(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
        created_by_type: str,
        title: str,
        group_id: uuid.UUID | None = None,
        assignee_agent_id: uuid.UUID | None = None,
        okr_key_result_id: uuid.UUID | None = None,
        description: str | None = None,
        priority: str | None = None,
        tags: list | None = None,
        artifact_paths: list | None = None,
    ) -> TaskCard:
        card = TaskCard(
            tenant_id=tenant_id,
            group_id=group_id,
            assignee_agent_id=assignee_agent_id,
            okr_key_result_id=okr_key_result_id,
            title=title,
            description=description,
            priority=priority,
            tags=tags or [],
            artifact_paths=artifact_paths or [],
            created_by=created_by,
            created_by_type=created_by_type,
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def get_card(
        self, card_id: uuid.UUID, *, tenant_id: uuid.UUID
    ) -> TaskCard | None:
        """Fetch a card, scoped to tenant. Returns None if not found OR cross-tenant."""
        result = await self.db.execute(
            select(TaskCard).where(
                TaskCard.id == card_id,
                TaskCard.tenant_id == tenant_id,
                TaskCard.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_cards_by_group(
        self, group_id: uuid.UUID, *, tenant_id: uuid.UUID
    ) -> Sequence[TaskCard]:
        """All cards on a group's board, ordered for Kanban rendering."""
        result = await self.db.execute(
            select(TaskCard)
            .where(
                TaskCard.group_id == group_id,
                TaskCard.tenant_id == tenant_id,
                TaskCard.deleted_at.is_(None),
            )
            .order_by(TaskCard.column, TaskCard.position, TaskCard.created_at)
        )
        return result.scalars().all()

    async def list_cards_by_assignee(
        self, agent_id: uuid.UUID, *, tenant_id: uuid.UUID
    ) -> Sequence[TaskCard]:
        """All cards assigned to an agent (their personal board)."""
        result = await self.db.execute(
            select(TaskCard)
            .where(
                TaskCard.assignee_agent_id == agent_id,
                TaskCard.tenant_id == tenant_id,
                TaskCard.deleted_at.is_(None),
            )
            .order_by(TaskCard.column, TaskCard.position)
        )
        return result.scalars().all()

    async def list_cards_by_ids(
        self,
        card_ids: Sequence[uuid.UUID],
        *,
        tenant_id: uuid.UUID,
    ) -> Sequence[TaskCard]:
        """Batch fetch by id (avoids N+1). Scoped to tenant."""
        if not card_ids:
            return []
        result = await self.db.execute(
            select(TaskCard).where(
                TaskCard.id.in_(list(card_ids)),
                TaskCard.tenant_id == tenant_id,
                TaskCard.deleted_at.is_(None),
            )
        )
        return result.scalars().all()
