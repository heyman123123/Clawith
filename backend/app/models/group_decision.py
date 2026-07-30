"""Group-level decision requests owned by the decision-maker agent."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GroupDecisionRequest(Base):
    """Audit + hang state for routine auto-apply and exception owner confirms."""

    __tablename__ = "group_decision_requests"
    __table_args__ = (
        CheckConstraint(
            "category IN ('routine', 'human_comms', 'external_deploy', 'finance', 'uncertain')",
            name="ck_group_decision_requests_category",
        ),
        CheckConstraint(
            "status IN ('pending_owner_confirm', 'approved', 'rejected', 'auto_applied', 'cancelled')",
            name="ck_group_decision_requests_status",
        ),
        Index("ix_group_decision_requests_group_status", "group_id", "status"),
        Index("ix_group_decision_requests_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_group_decision_requests_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_group_decision_requests_group_id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("group_workflows.id", name="fk_group_decision_requests_workflow_id", ondelete="SET NULL"),
        nullable=True,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("group_workflow_stages.id", name="fk_group_decision_requests_stage_id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_maker_participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "participants.id",
            name="fk_group_decision_requests_decision_maker_participant_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    options_json: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending_owner_confirm'")
    )
    approver_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "participants.id",
            name="fk_group_decision_requests_approver_participant_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
