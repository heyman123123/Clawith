"""HR review board session records."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HrReviewSession(Base):
    """One HR review request (team building or governance top-up)."""

    __tablename__ = "hr_review_sessions"
    __table_args__ = (
        CheckConstraint(
            "session_type IN ('team_building', 'governance_topup')",
            name="ck_hr_review_sessions_session_type",
        ),
        CheckConstraint(
            "status IN ('open', 'user_selected', 'completed')",
            name="ck_hr_review_sessions_status",
        ),
        Index("ix_hr_review_sessions_group_id", "group_id"),
        Index("ix_hr_review_sessions_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_hr_review_sessions_group_id_groups", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "chat_sessions.id",
            name="fk_hr_review_sessions_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    session_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", server_default=text("'open'")
    )
    proposals: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    selected_proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
