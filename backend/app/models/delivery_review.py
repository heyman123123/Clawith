"""Delivery approval + dual-dimension scoring (P3 / P7 hardening).

需求 §4.11 + §8.3 specify:

* Delivery manager reviews **coverage** (40% of final score).
* Quality officer reviews **quality** (60% of final score).
* Pass threshold: ``final_score >= 90`` to archive; otherwise the workflow
  goes back into a rectification loop (max 3 attempts before warning).

This module declares the schema + a tiny result-helper so the scorer can
be tested without bringing in Pydantic-shaped DTOs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def text_value(sql: str):
    from sqlalchemy import text as _text

    return _text(sql)


class WorkflowDeliveryApproval(Base):
    """One row per delivery-review round (max 3 attempts)."""

    __tablename__ = "workflow_delivery_approvals"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_delivery_approvals"),
        CheckConstraint(
            "decision IN ('pending','approved','rejected','withdrawn')",
            name="ck_workflow_delivery_approvals_decision",
        ),
        CheckConstraint(
            "round_no >= 1 AND round_no <= 3",
            name="ck_workflow_delivery_approvals_round",
        ),
        Index(
            "ix_workflow_delivery_approvals_workflow",
            "workflow_id",
            "round_no",
        ),
        Index("ix_workflow_delivery_approvals_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_workflow_delivery_approvals_tenant_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "workflow_runs.id",
            name="fk_workflow_delivery_approvals_workflow_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text_value("1"))
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text_value("'pending'")
    )
    delivery_manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_workflow_delivery_approvals_manager",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    quality_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agents.id",
            name="fk_workflow_delivery_approvals_quality_agent",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    coverage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rectification_items: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowHumanReview(Base):
    """Cross-cutting human review queue: high-risk skills, qc anomalies, shareholder halts.

    Single table covers the three review channels called out in
    需求 §3.4 (审批卡 / 决策卡 / 高危技能审核 / 质检异常人工复核).
    """

    __tablename__ = "workflow_human_reviews"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_human_reviews"),
        CheckConstraint(
            "kind IN ('high_risk_skill','qc_anomaly_rectification','"
            "shareholder_decision','approval_card','decision_card','rectification')",
            name="ck_workflow_human_reviews_kind",
        ),
        CheckConstraint(
            "status IN ('open','approved','rejected','withdrawn','auto_resolved')",
            name="ck_workflow_human_reviews_status",
        ),
        Index("ix_workflow_human_reviews_kind_status", "kind", "status"),
        Index("ix_workflow_human_reviews_workflow", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_workflow_human_reviews_tenant_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "workflow_runs.id",
            name="fk_workflow_human_reviews_workflow_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skills.id",
            name="fk_workflow_human_reviews_skill_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agents.id",
            name="fk_workflow_human_reviews_agent_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default=text_value("'open'")
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text_value("'{}'::jsonb")
    )
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_workflow_human_reviews_requester",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_workflow_human_reviews_reviewer",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "WorkflowDeliveryApproval",
    "WorkflowHumanReview",
]
