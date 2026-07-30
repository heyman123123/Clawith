"""Durable jobs for notifying group leaders about failed Runs (and model probes)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GroupRunResumeJob(Base):
    """One recovery/notify job per failed group Run."""

    __tablename__ = "group_run_resume_jobs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('general', 'model_quota')",
            name="ck_group_run_resume_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'notified', 'recovered_notified', 'timed_out', 'cancelled')",
            name="ck_group_run_resume_jobs_status",
        ),
        UniqueConstraint("failed_run_id", name="uq_group_run_resume_jobs_failed_run_id"),
        Index("ix_group_run_resume_jobs_pending_check", "status", "next_check_at"),
        Index("ix_group_run_resume_jobs_group", "group_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_group_run_resume_jobs_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_group_run_resume_jobs_group_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", name="fk_group_run_resume_jobs_session_id", ondelete="CASCADE"),
        nullable=False,
    )
    failed_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", name="fk_group_run_resume_jobs_failed_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    failed_agent_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "participants.id",
            name="fk_group_run_resume_jobs_failed_agent_participant_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    error_code: Mapped[str] = mapped_column(String(120), nullable=False, server_default=text("''"))
    error_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1800"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    leader_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
