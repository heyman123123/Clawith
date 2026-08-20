"""ChiefRun — lifecycle metadata for a Group's Persistent Chief Runtime.

The Chief's actual execution state (decisions, conversation history,
pending actions) lives in the LangGraph Checkpoint (single source of
truth per C1). This table holds only:
  - which Group the Chief is for (1:1, enforced by UNIQUE group_id)
  - which Agent is the Chief (chief_agent_id)
  - the LangGraph thread_id used to resume the Checkpoint
  - lifecycle status (active / paused / degraded / stopped)
  - failure counter for deadlock avoidance (5 failures -> degraded)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChiefRun(Base):
    """Persistent Chief Runtime metadata for one Group."""

    __tablename__ = "chief_runs"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_chief_runs_group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    chief_agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    runtime_thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active", index=True)
    # Values: active / paused / degraded / stopped
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_failure_reason: Mapped[str | None] = mapped_column(nullable=True)
