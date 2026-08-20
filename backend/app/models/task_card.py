"""TaskCard model — kanban card for the Intent-Driven Orchestrator task board.

Each card lives either on a Group board (group_id set) or an Agent's
personal board (group_id NULL, assignee_agent_id set). Cards may be
statically derived from a Draft's TaskProposal OR dynamically added by
the Chief Runtime or executing agents during a project (the
"tasks-emerge-during-execution" requirement).

State machine: TaskColumn (backlog/in_progress/blocked/review/done).
Optimistic lock: version increments on every move; clients must send
the version they read to detect concurrent updates.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskCard(Base):
    """A kanban card on the Group or Agent task board."""

    __tablename__ = "task_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    assignee_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    okr_key_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    column: Mapped[str] = mapped_column(String(32), nullable=False, server_default="backlog")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # opt-in time fields - populated only when user explicitly specifies
    target_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifact_paths: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Values: user / agent / chief
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
