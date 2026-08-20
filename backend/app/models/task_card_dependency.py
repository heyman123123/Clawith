"""TaskCardDependency — directed edge in the task DAG.

A TaskCard A may depend on TaskCard B (B blocks A). The DAG is validated
on insert by TaskBoardService to prevent cycles (T-010 acceptance).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskCardDependency(Base):
    """Edge: task_card_id depends on depends_on_card_id."""

    __tablename__ = "task_card_dependencies"
    __table_args__ = (
        UniqueConstraint("task_card_id", "depends_on_card_id", name="uq_task_card_deps_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    task_card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    depends_on_card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    dependency_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="blocks")
    # Values: blocks / informs / references
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
