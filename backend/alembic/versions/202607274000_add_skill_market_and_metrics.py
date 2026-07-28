"""Add skill marketplace + workflow metrics + template matches (P5 / P6).

Revision ID: add_skill_market_and_metrics
Revises: add_evolution_signal_and_drafts
Create Date: 2026-07-27 24:00:00

Six new tables back the P5 / P6 features:

* ``skill_market_listings``         — published marketplace listings.
* ``skill_sandbox_runs``           — sandboxed smoke-test runs.
* ``skill_approval_requests``      — high-risk publish approvals.
* ``skill_learning_records``       — per-agent learning audit row.
* ``agent_skill_bindings``         — installed per-agent bindings.
* ``workflow_templates``           — curated catalog of reusable flows.
* ``workflow_metrics_daily``       — one row per tenant per day.
* ``workflow_template_match_events``— log of every Top-3 match.

All seven tables cascade on tenant / parent deletes.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "add_skill_market_and_metrics"
down_revision: str | None = "add_evolution_signal_and_drafts"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()

    if "skill_market_listings" not in tables:
        op.create_table(
            "skill_market_listings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column(
                "keywords",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("share_scope", sa.String(length=16), nullable=False, server_default="team"),
            sa.Column("install_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("publisher_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["publisher_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name="pk_skill_market_listings"),
            sa.UniqueConstraint("skill_id", name="uq_skill_market_listings_skill_id"),
            sa.CheckConstraint(
                "status IN ('draft','in_review','published','disabled','rejected')",
                name="ck_skill_market_listings_status",
            ),
            sa.CheckConstraint(
                "risk_level IN ('low','medium','high')",
                name="ck_skill_market_listings_risk_level",
            ),
            sa.CheckConstraint(
                "share_scope IN ('private','team','company')",
                name="ck_skill_market_listings_share_scope",
            ),
        )

    listing_indexes = _indexes("skill_market_listings")
    if "ix_skill_market_listings_status" not in listing_indexes:
        op.create_index("ix_skill_market_listings_status", "skill_market_listings", ["status"])
    if "ix_skill_market_listings_tenant_id" not in listing_indexes:
        op.create_index(
            "ix_skill_market_listings_tenant_id", "skill_market_listings", ["tenant_id"]
        )

    if "skill_sandbox_runs" not in tables:
        op.create_table(
            "skill_sandbox_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("triggered_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("language", sa.String(length=16), nullable=False, server_default="python"),
            sa.Column("code_excerpt", sa.Text(), nullable=True),
            sa.Column("stdout", sa.Text(), nullable=True),
            sa.Column("stderr", sa.Text(), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("detected_risk_level", sa.String(length=16), nullable=False, server_default="low"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["listing_id"], ["skill_market_listings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name="pk_skill_sandbox_runs"),
            sa.CheckConstraint(
                "status IN ('queued','running','succeeded','failed','timeout','blocked')",
                name="ck_skill_sandbox_runs_status",
            ),
            sa.CheckConstraint(
                "language IN ('python','bash','node')",
                name="ck_skill_sandbox_runs_language",
            ),
        )

    sandbox_indexes = _indexes("skill_sandbox_runs")
    if "ix_skill_sandbox_runs_listing_id" not in sandbox_indexes:
        op.create_index(
            "ix_skill_sandbox_runs_listing_id", "skill_sandbox_runs", ["listing_id"]
        )
    if "ix_skill_sandbox_runs_tenant_id" not in sandbox_indexes:
        op.create_index(
            "ix_skill_sandbox_runs_tenant_id", "skill_sandbox_runs", ["tenant_id"]
        )

    if "skill_approval_requests" not in tables:
        op.create_table(
            "skill_approval_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sandbox_run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="high_risk_publish"),
            sa.Column("decision", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("requester_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("decision_notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["listing_id"], ["skill_market_listings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["sandbox_run_id"], ["skill_sandbox_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name="pk_skill_approval_requests"),
            sa.CheckConstraint(
                "decision IN ('pending','approved','rejected')",
                name="ck_skill_approval_requests_decision",
            ),
            sa.CheckConstraint(
                "kind IN ('high_risk_publish','high_risk_install','policy_exception')",
                name="ck_skill_approval_requests_kind",
            ),
        )

    approval_indexes = _indexes("skill_approval_requests")
    if "ix_skill_approval_requests_decision" not in approval_indexes:
        op.create_index(
            "ix_skill_approval_requests_decision",
            "skill_approval_requests",
            ["decision"],
        )
    if "ix_skill_approval_requests_listing_id" not in approval_indexes:
        op.create_index(
            "ix_skill_approval_requests_listing_id",
            "skill_approval_requests",
            ["listing_id"],
        )

    if "skill_learning_records" not in tables:
        op.create_table(
            "skill_learning_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("trigger_reason", sa.Text(), nullable=True),
            sa.Column("detected_gap", sa.Text(), nullable=True),
            sa.Column("sandbox_run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("detected_risk_level", sa.String(length=16), nullable=False, server_default="low"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="detecting"),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["listing_id"], ["skill_market_listings.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["sandbox_run_id"], ["skill_sandbox_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["approval_id"], ["skill_approval_requests.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name="pk_skill_learning_records"),
            sa.CheckConstraint(
                "status IN ('detecting','sandboxing','awaiting_approval','bound','rejected','failed')",
                name="ck_skill_learning_records_status",
            ),
        )

    learning_indexes = _indexes("skill_learning_records")
    if "ix_skill_learning_records_agent" not in learning_indexes:
        op.create_index("ix_skill_learning_records_agent", "skill_learning_records", ["agent_id"])
    if "ix_skill_learning_records_tenant_id" not in learning_indexes:
        op.create_index(
            "ix_skill_learning_records_tenant_id", "skill_learning_records", ["tenant_id"]
        )

    if "agent_skill_bindings" not in tables:
        op.create_table(
            "agent_skill_bindings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("learning_record_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "installed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["learning_record_id"],
                ["skill_learning_records.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_skill_bindings"),
            sa.UniqueConstraint(
                "agent_id", "skill_id", name="uq_agent_skill_bindings_agent_skill"
            ),
        )

    binding_indexes = _indexes("agent_skill_bindings")
    if "ix_agent_skill_bindings_agent" not in binding_indexes:
        op.create_index(
            "ix_agent_skill_bindings_agent", "agent_skill_bindings", ["agent_id"]
        )

    if "workflow_templates" not in tables:
        op.create_table(
            "workflow_templates",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column(
                "tags",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "keywords",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "recommended_roles",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("quality_threshold", sa.Integer(), nullable=False, server_default=sa.text("80")),
            sa.Column("ao_provider", sa.String(length=50), nullable=True),
            sa.Column("ao_model", sa.String(length=100), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("usage_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_workflow_templates"),
            sa.UniqueConstraint(
                "tenant_id", "slug", name="uq_workflow_templates_tenant_slug"
            ),
            sa.CheckConstraint(
                "status IN ('draft','published','deprecated')",
                name="ck_workflow_templates_status",
            ),
        )

    template_indexes = _indexes("workflow_templates")
    if "ix_workflow_templates_status" not in template_indexes:
        op.create_index(
            "ix_workflow_templates_status", "workflow_templates", ["status"]
        )
    if "ix_workflow_templates_tenant_id" not in template_indexes:
        op.create_index(
            "ix_workflow_templates_tenant_id", "workflow_templates", ["tenant_id"]
        )

    if "workflow_metrics_daily" not in tables:
        op.create_table(
            "workflow_metrics_daily",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("metric_date", sa.Date(), nullable=False),
            *[
                sa.Column(col, sa.Integer(), nullable=False, server_default=sa.text("0"))
                for col in (
                    "workflows_started",
                    "workflows_succeeded",
                    "workflows_failed",
                    "steps_dispatched",
                    "steps_quality_passed",
                    "steps_quality_failed",
                    "steps_delivery_approved",
                    "steps_delivery_rejected",
                    "sandbox_runs_total",
                    "sandbox_runs_blocked",
                    "skill_learning_total",
                    "skill_learning_approved",
                    "skill_learning_rejected",
                    "evolution_events",
                    "evolution_rollbacks",
                    "tokens_input_total",
                    "tokens_output_total",
                )
            ],
            sa.Column("quality_score_avg", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_workflow_metrics_daily"),
            sa.UniqueConstraint(
                "tenant_id", "metric_date", name="uq_workflow_metrics_daily_tenant_date"
            ),
        )

    daily_indexes = _indexes("workflow_metrics_daily")
    if "ix_workflow_metrics_daily_tenant_date" not in daily_indexes:
        op.create_index(
            "ix_workflow_metrics_daily_tenant_date",
            "workflow_metrics_daily",
            ["tenant_id", "metric_date"],
        )

    if "workflow_template_match_events" not in tables:
        op.create_table(
            "workflow_template_match_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("requirements_excerpt", sa.Text(), nullable=True),
            sa.Column("match_score", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("rank", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("outcome", sa.String(length=16), nullable=False, server_default="presented"),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["template_id"], ["workflow_templates.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name="pk_workflow_template_match_events"),
            sa.CheckConstraint(
                "outcome IN ('presented','selected','dismissed')",
                name="ck_workflow_template_match_events_outcome",
            ),
        )

    match_indexes = _indexes("workflow_template_match_events")
    if "ix_workflow_template_match_events_tenant_id" not in match_indexes:
        op.create_index(
            "ix_workflow_template_match_events_tenant_id",
            "workflow_template_match_events",
            ["tenant_id"],
        )
    if "ix_workflow_template_match_events_template" not in match_indexes:
        op.create_index(
            "ix_workflow_template_match_events_template",
            "workflow_template_match_events",
            ["template_id"],
        )


def downgrade() -> None:
    tables = _tables()

    match_indexes = _indexes("workflow_template_match_events") if "workflow_template_match_events" in tables else set()
    for idx in (
        "ix_workflow_template_match_events_template",
        "ix_workflow_template_match_events_tenant_id",
    ):
        if idx in match_indexes:
            op.drop_index(idx, table_name="workflow_template_match_events")
    if "workflow_template_match_events" in tables:
        op.drop_table("workflow_template_match_events")

    daily_indexes = _indexes("workflow_metrics_daily") if "workflow_metrics_daily" in tables else set()
    for idx in ("ix_workflow_metrics_daily_tenant_date",):
        if idx in daily_indexes:
            op.drop_index(idx, table_name="workflow_metrics_daily")
    if "workflow_metrics_daily" in tables:
        op.drop_table("workflow_metrics_daily")

    template_indexes = _indexes("workflow_templates") if "workflow_templates" in tables else set()
    for idx in ("ix_workflow_templates_tenant_id", "ix_workflow_templates_status"):
        if idx in template_indexes:
            op.drop_index(idx, table_name="workflow_templates")
    if "workflow_templates" in tables:
        op.drop_table("workflow_templates")

    binding_indexes = _indexes("agent_skill_bindings") if "agent_skill_bindings" in tables else set()
    for idx in ("ix_agent_skill_bindings_agent",):
        if idx in binding_indexes:
            op.drop_index(idx, table_name="agent_skill_bindings")
    if "agent_skill_bindings" in tables:
        op.drop_table("agent_skill_bindings")

    learning_indexes = _indexes("skill_learning_records") if "skill_learning_records" in tables else set()
    for idx in (
        "ix_skill_learning_records_tenant_id",
        "ix_skill_learning_records_agent",
    ):
        if idx in learning_indexes:
            op.drop_index(idx, table_name="skill_learning_records")
    if "skill_learning_records" in tables:
        op.drop_table("skill_learning_records")

    approval_indexes = _indexes("skill_approval_requests") if "skill_approval_requests" in tables else set()
    for idx in (
        "ix_skill_approval_requests_listing_id",
        "ix_skill_approval_requests_decision",
    ):
        if idx in approval_indexes:
            op.drop_index(idx, table_name="skill_approval_requests")
    if "skill_approval_requests" in tables:
        op.drop_table("skill_approval_requests")

    sandbox_indexes = _indexes("skill_sandbox_runs") if "skill_sandbox_runs" in tables else set()
    for idx in (
        "ix_skill_sandbox_runs_tenant_id",
        "ix_skill_sandbox_runs_listing_id",
    ):
        if idx in sandbox_indexes:
            op.drop_index(idx, table_name="skill_sandbox_runs")
    if "skill_sandbox_runs" in tables:
        op.drop_table("skill_sandbox_runs")

    listing_indexes = _indexes("skill_market_listings") if "skill_market_listings" in tables else set()
    for idx in (
        "ix_skill_market_listings_tenant_id",
        "ix_skill_market_listings_status",
    ):
        if idx in listing_indexes:
            op.drop_index(idx, table_name="skill_market_listings")
    if "skill_market_listings" in tables:
        op.drop_table("skill_market_listings")