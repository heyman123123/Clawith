"""Workflow templates + daily metrics + template match scoring (P6).

Three tables back P6:

* ``workflow_templates`` — curated catalog rows (title / summary /
  default roles / tags). The :class:`app.models.project.ProjectWorkflow`
  already exposes ``template_key`` as a free string; this catalog gives
  HR a discovery surface and Top-3 matching.
* ``workflow_metrics_daily`` — aggregated counts (workflows started,
  steps dispatched, quality scores, evolution events, sandbox runs,
  approval latency). One row per tenant + day.
* ``workflow_template_match_events`` — every Top-3 match presented to
  HR is logged so admins can A/B the algorithm later.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def text_value(sql: str):
    """Local helper for ``server_default=text(sql)`` payloads."""
    from sqlalchemy import text as _text

    return _text(sql)


class WorkflowTemplate(Base):
    """A reusable workflow blueprint published in the company catalog."""

    __tablename__ = "workflow_templates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_templates"),
        UniqueConstraint("tenant_id", "slug", name="uq_workflow_templates_tenant_slug"),
        CheckConstraint(
            "status IN ('draft','published','deprecated')",
            name="ck_workflow_templates_status",
        ),
        Index("ix_workflow_templates_status", "status"),
        Index("ix_workflow_templates_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_workflow_templates_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb"))
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb"))
    recommended_roles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb"))
    quality_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=80, server_default=text_value("80"))
    ao_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ao_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default=text_value("'draft'"))
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WorkflowMetricDaily(Base):
    """One row per tenant per day — aggregated KPI for the dashboard."""

    __tablename__ = "workflow_metrics_daily"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_metrics_daily"),
        UniqueConstraint("tenant_id", "metric_date", name="uq_workflow_metrics_daily_tenant_date"),
        Index("ix_workflow_metrics_daily_tenant_date", "tenant_id", "metric_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_workflow_metrics_daily_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    workflows_started: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    workflows_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    workflows_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    steps_dispatched: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    steps_quality_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    steps_quality_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    quality_score_avg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text_value("0"))
    steps_delivery_approved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    steps_delivery_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    sandbox_runs_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    sandbox_runs_blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    skill_learning_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    skill_learning_approved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    skill_learning_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    evolution_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    evolution_rollbacks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    tokens_input_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    tokens_output_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WorkflowTemplateMatchEvent(Base):
    """One Top-3 match presented to HR (analytics for matching algorithm)."""

    __tablename__ = "workflow_template_match_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_template_match_events"),
        CheckConstraint(
            "outcome IN ('presented','selected','dismissed')",
            name="ck_workflow_template_match_events_outcome",
        ),
        Index("ix_workflow_template_match_events_tenant_id", "tenant_id"),
        Index("ix_workflow_template_match_events_template", "template_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_workflow_template_match_events_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_templates.id", name="fk_workflow_template_match_events_template_id", ondelete="CASCADE"),
        nullable=False,
    )
    requirements_excerpt: Mapped[str] = mapped_column(Text, default="")
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="presented", server_default=text_value("'presented'"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_workflow_template_match_events_user", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "WorkflowMetricDaily",
    "WorkflowTemplate",
    "WorkflowTemplateMatchEvent",
]  # text_value is exported implicitly via the module-level helper.
