"""Skill marketplace schema (P5).

Adds three tables:

* ``skill_market_listings`` — published skill catalog entries. Each row
  wraps an existing :class:`app.models.skill.Skill` and adds marketplace
  metadata (status, risk_level, install_count, share_scope).
* ``skill_sandbox_runs`` — one row per sandboxed smoke-test run of a
  skill before it goes live (``status``, ``risk_level``, ``stdout``,
  ``stderr``, ``duration_ms``).
* ``skill_approval_requests`` — high-risk skills require human approval
  before publish. The row carries ``decision`` + ``reviewer_id`` when
  resolved.
* ``skill_learning_records`` — one row per skill-learning attempt
  (success / failure, sandboxes run, approval state, resulting binding).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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


class SkillMarketListing(Base):
    """One entry in the company-wide skill marketplace."""

    __tablename__ = "skill_market_listings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skill_market_listings"),
        UniqueConstraint("skill_id", name="uq_skill_market_listings_skill_id"),
        CheckConstraint(
            "status IN ('draft','in_review','published','disabled','rejected')",
            name="ck_skill_market_listings_status",
        ),
        CheckConstraint(
            "risk_level IN ('low','medium','high')",
            name="ck_skill_market_listings_risk_level",
        ),
        CheckConstraint(
            "share_scope IN ('private','team','company')",
            name="ck_skill_market_listings_share_scope",
        ),
        Index("ix_skill_market_listings_status", "status"),
        Index("ix_skill_market_listings_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_skill_market_listings_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", name="fk_skill_market_listings_skill_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb"))
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low", server_default=text_value("'low'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default=text_value("'draft'"))
    share_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="team", server_default=text_value("'team'"))
    install_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    publisher_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_skill_market_listings_publisher", ondelete="SET NULL"),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SkillSandboxRun(Base):
    """One sandbox execution attached to a skill listing."""

    __tablename__ = "skill_sandbox_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skill_sandbox_runs"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','timeout','blocked')",
            name="ck_skill_sandbox_runs_status",
        ),
        CheckConstraint(
            "language IN ('python','bash','node')",
            name="ck_skill_sandbox_runs_language",
        ),
        Index("ix_skill_sandbox_runs_listing_id", "listing_id"),
        Index("ix_skill_sandbox_runs_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_skill_sandbox_runs_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_market_listings.id",
            name="fk_skill_sandbox_runs_listing_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", name="fk_skill_sandbox_runs_skill_id", ondelete="CASCADE"),
        nullable=True,
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_skill_sandbox_runs_user", ondelete="SET NULL"),
        nullable=True,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="python", server_default=text_value("'python'"))
    code_excerpt: Mapped[str] = mapped_column(Text, default="")
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", server_default=text_value("'queued'"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_risk_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="low", server_default=text_value("'low'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SkillApprovalRequest(Base):
    """High-risk skill publish needs human sign-off."""

    __tablename__ = "skill_approval_requests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skill_approval_requests"),
        CheckConstraint(
            "decision IN ('pending','approved','rejected')",
            name="ck_skill_approval_requests_decision",
        ),
        CheckConstraint(
            "kind IN ('high_risk_publish','high_risk_install','policy_exception')",
            name="ck_skill_approval_requests_kind",
        ),
        Index("ix_skill_approval_requests_decision", "decision"),
        Index("ix_skill_approval_requests_listing_id", "listing_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_skill_approval_requests_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_market_listings.id",
            name="fk_skill_approval_requests_listing_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    sandbox_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_sandbox_runs.id",
            name="fk_skill_approval_requests_sandbox_run_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text_value("'high_risk_publish'"))
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default=text_value("'pending'"))
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_skill_approval_requests_user", ondelete="SET NULL"),
        nullable=True,
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_skill_approval_requests_reviewer", ondelete="SET NULL"),
        nullable=True,
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillLearningRecord(Base):
    """One attempt of an agent learning a skill (whether published or rejected)."""

    __tablename__ = "skill_learning_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skill_learning_records"),
        CheckConstraint(
            "status IN ('detecting','sandboxing','awaiting_approval','bound','rejected','failed')",
            name="ck_skill_learning_records_status",
        ),
        Index("ix_skill_learning_records_agent", "agent_id"),
        Index("ix_skill_learning_records_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_skill_learning_records_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_skill_learning_records_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", name="fk_skill_learning_records_skill_id", ondelete="SET NULL"),
        nullable=True,
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_market_listings.id",
            name="fk_skill_learning_records_listing_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_gap: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_sandbox_runs.id",
            name="fk_skill_learning_records_sandbox_run_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_approval_requests.id",
            name="fk_skill_learning_records_approval_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    detected_risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low", server_default=text_value("'low'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="detecting", server_default=text_value("'detecting'"))
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentSkillBinding(Base):
    """Per-agent binding to a published skill (``agent_id × skill_id``)."""

    __tablename__ = "agent_skill_bindings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_skill_bindings"),
        UniqueConstraint("agent_id", "skill_id", name="uq_agent_skill_bindings_agent_skill"),
        Index("ix_agent_skill_bindings_agent", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_skill_bindings_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_skill_bindings_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", name="fk_agent_skill_bindings_skill_id", ondelete="CASCADE"),
        nullable=False,
    )
    learning_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_learning_records.id",
            name="fk_agent_skill_bindings_learning_record",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "AgentSkillBinding",
    "SkillApprovalRequest",
    "SkillLearningRecord",
    "SkillMarketListing",
    "SkillSandboxRun",
]  # text_value is exported implicitly via the module-level helper.
