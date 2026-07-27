"""Board escalation records linking decision groups to shareholder sessions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BoardEscalation(Base):
    """One decision-group escalation opened in the tenant shareholder board."""

    __tablename__ = "board_escalations"
    __table_args__ = (
        Index(
            "uq_board_escalations_open_decision_session",
            "decision_session_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_board_escalations_tenant_id", "tenant_id"),
        Index("ix_board_escalations_workflow_id", "workflow_id"),
        Index("ix_board_escalations_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    shareholder_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    shareholder_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    # open | resolved | dispatched
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    escalation_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    board_resolution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
