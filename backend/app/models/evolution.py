"""Evolution engine schema (P3).

Mirrors the ``202607272000_add_evolution_records`` Alembic migration. Two
tables:

* ``agent_role_versions`` — append-only history of every soul/prompt change.
  ``is_current`` flips so rollback can flip a previous version back to
  ``is_current=True`` atomically.
* ``agent_evolution_records`` — one row per "evolution event". Pairs the
  version that produced the regression (from_version_id) with the version
  the engine rolled out (to_version_id) and the verdict context that
  motivated the change.

Both tables cascade on Agent / AgentTemplate delete so tenant data wipes
stay simple.
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def text_value(sql: str):
    """Render a raw SQL fragment as a server_default literal."""
    return text(sql)


class AgentRoleVersion(Base):
    """One immutable version of an Agent's soul_md.

    Either ``agent_id`` (per-tenant instance) or ``agent_template_id`` is
    populated; the other is NULL. ``version_no`` is per-target monotonic —
    P3 enforces the uniqueness via the unique constraints below.
    """

    __tablename__ = "agent_role_versions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_role_versions"),
        UniqueConstraint(
            "agent_id",
            "version_no",
            name="uq_agent_role_versions_agent_version",
        ),
        UniqueConstraint(
            "agent_template_id",
            "version_no",
            name="uq_agent_role_versions_template_version",
        ),
        CheckConstraint(
            "source IN ('baseline','evolution','rollback','manual')",
            name="ck_agent_role_versions_source",
        ),
        CheckConstraint(
            "(agent_id IS NOT NULL)::int + (agent_template_id IS NOT NULL)::int = 1",
            name="ck_agent_role_versions_target_exactly_one",
        ),
        Index("ix_agent_role_versions_agent_id", "agent_id"),
        Index("ix_agent_role_versions_template_id", "agent_template_id"),
        Index("ix_agent_role_versions_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_role_versions_tenant_id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_role_versions_agent_id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_templates.id",
            name="fk_agent_role_versions_agent_template_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    soul_md: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="baseline", server_default="baseline"
    )
    evolution_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_agent_role_versions_user", ondelete="SET NULL"),
        nullable=True,
    )


class AgentEvolutionRecord(Base):
    """Audit row describing one evolution event (or rollback)."""

    __tablename__ = "agent_evolution_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_evolution_records"),
        CheckConstraint(
            "kind IN ('baseline','evolution','rollback','manual')",
            name="ck_agent_evolution_records_kind",
        ),
        Index("ix_agent_evolution_records_agent_id", "agent_id"),
        Index("ix_agent_evolution_records_tenant_id", "tenant_id"),
        Index("ix_agent_evolution_records_kind", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_evolution_records_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_evolution_records_agent_id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_templates.id",
            name="fk_agent_evolution_records_agent_template_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_role_versions.id",
            name="fk_agent_evolution_records_from_version",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    to_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_role_versions.id",
            name="fk_agent_evolution_records_to_version",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    quality_score_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_score_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_agent_evolution_records_user", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentEvolutionSignal(Base):
    """One quality signal that *might* lead to a soul patch (P4).

    Every time an agent's step passes the rule engine, P3 writes a tiny
    row here capturing the verdict, the LLM-judge payload (if used) and
    any free-form reasons/comments the judge surfaced. P4's
    :mod:`app.services.ao.patch_engine` reads recent signals to draft a
    new soul candidate, which then goes through the regression harness.
    """

    __tablename__ = "agent_evolution_signals"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_evolution_signals"),
        CheckConstraint(
            "kind IN ('quality_passed','judge_flagged','manual')",
            name="ck_agent_evolution_signals_kind",
        ),
        Index("ix_agent_evolution_signals_agent_created", "agent_id", "created_at"),
        Index("ix_agent_evolution_signals_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_evolution_signals_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_evolution_signals_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="quality_passed")
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    judge_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reasons: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentEvolutionDraft(Base):
    """A draft soul produced by :mod:`patch_engine` that needs harness validation."""

    __tablename__ = "agent_evolution_drafts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_evolution_drafts"),
        CheckConstraint(
            "status IN ('pending','running','accepted','rejected','failed')",
            name="ck_agent_evolution_drafts_status",
        ),
        CheckConstraint(
            "patch_strategy IN ('append_rules','replace_summary','no_op')",
            name="ck_agent_evolution_drafts_patch_strategy",
        ),
        Index("ix_agent_evolution_drafts_agent_status", "agent_id", "status"),
        Index("ix_agent_evolution_drafts_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_evolution_drafts_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_evolution_drafts_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    baseline_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_role_versions.id",
            name="fk_agent_evolution_drafts_baseline_version",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    patch_strategy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="append_rules", server_default="append_rules"
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_additions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    draft_soul_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default=text_value("'pending'")
    )
    decline_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_signal_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentHarnessFixture(Base):
    """One frozen prompt / expectation pair used by the regression harness."""

    __tablename__ = "agent_harness_fixtures"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_harness_fixtures"),
        CheckConstraint(
            "kind IN ('role_skill','role_qa','role_style','custom')",
            name="ck_agent_harness_fixtures_kind",
        ),
        Index("ix_agent_harness_fixtures_agent_role", "agent_id", "fixture_role"),
        Index("ix_agent_harness_fixtures_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_harness_fixtures_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_harness_fixtures_agent_id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_templates.id",
            name="fk_agent_harness_fixtures_agent_template_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    fixture_role: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="role_qa")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    task_summary: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_keywords: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    rubric: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text_value("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentHarnessRun(Base):
    """One harness evaluation against one draft or one published version.

    ``stage`` is either ``baseline`` (run against the published baseline
    soul) or ``candidate`` (run against a draft). The :func:`evaluate
    candidate` orchestration always runs both stages so the gating logic
    can compare them in a single transaction.
    """

    __tablename__ = "agent_harness_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_harness_runs"),
        CheckConstraint(
            "stage IN ('baseline','candidate')",
            name="ck_agent_harness_runs_stage",
        ),
        CheckConstraint(
            "status IN ('running','succeeded','failed','cancelled')",
            name="ck_agent_harness_runs_status",
        ),
        Index("ix_agent_harness_runs_draft_stage", "draft_id", "stage"),
        Index("ix_agent_harness_runs_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_agent_harness_runs_tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_agent_harness_runs_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agent_evolution_drafts.id",
            name="fk_agent_harness_runs_draft_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default=text_value("'running'")
    )
    average_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    fixture_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    per_fixture: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AgentEvolutionDraft",
    "AgentEvolutionRecord",
    "AgentEvolutionSignal",
    "AgentHarnessFixture",
    "AgentHarnessRun",
    "AgentRoleVersion",
]
