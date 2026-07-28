"""Add evolution signal / draft / harness tables (P4).

Revision ID: add_evolution_signal_and_drafts
Revises: add_evolution_records
Create Date: 2026-07-27 23:00:00

Adds three tables on top of the P3 ``agent_role_versions`` machinery so
the patch engine / regression harness can iterate without touching any of
the existing tables:

* ``agent_evolution_signals`` — immutable audit row per quality signal.
* ``agent_evolution_drafts``  — candidate soul produced by
  :mod:`app.services.ao.patch_engine`; holds the new soul text plus the
  source signal ids.
* ``agent_harness_fixtures``  — frozen prompts / acceptance criteria the
  harness uses for side-by-side scoring.
* ``agent_harness_runs``      — one row per harness stage (``baseline`` /
  ``candidate``); ``average_score`` drives the apply / reject decision.

All four tables cascade on tenant / agent / template delete.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "add_evolution_signal_and_drafts"
down_revision: str | None = "add_evolution_records"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()

    if "agent_evolution_signals" not in tables:
        op.create_table(
            "agent_evolution_signals",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="quality_passed"),
            sa.Column("trigger_source", sa.String(length=64), nullable=False),
            sa.Column("trigger_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("quality_score", sa.Integer(), nullable=True),
            sa.Column("rule_score", sa.Integer(), nullable=True),
            sa.Column("judge_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("judge_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "reasons",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_agent_evolution_signals_tenant_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_agent_evolution_signals_agent_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_evolution_signals"),
            sa.CheckConstraint(
                "kind IN ('quality_passed','judge_flagged','manual')",
                name="ck_agent_evolution_signals_kind",
            ),
        )

    signal_indexes = _indexes("agent_evolution_signals")
    if "ix_agent_evolution_signals_agent_created" not in signal_indexes:
        op.create_index(
            "ix_agent_evolution_signals_agent_created",
            "agent_evolution_signals",
            ["agent_id", "created_at"],
        )
    if "ix_agent_evolution_signals_tenant_id" not in signal_indexes:
        op.create_index(
            "ix_agent_evolution_signals_tenant_id",
            "agent_evolution_signals",
            ["tenant_id"],
        )

    if "agent_evolution_drafts" not in tables:
        op.create_table(
            "agent_evolution_drafts",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("baseline_version_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "patch_strategy",
                sa.String(length=32),
                nullable=False,
                server_default="append_rules",
            ),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column(
                "rule_additions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("draft_soul_md", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("decline_reason", sa.String(length=500), nullable=True),
            sa.Column(
                "source_signal_ids",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
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
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_agent_evolution_drafts_tenant_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_agent_evolution_drafts_agent_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["baseline_version_id"],
                ["agent_role_versions.id"],
                name="fk_agent_evolution_drafts_baseline_version",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_evolution_drafts"),
            sa.CheckConstraint(
                "status IN ('pending','running','accepted','rejected','failed')",
                name="ck_agent_evolution_drafts_status",
            ),
            sa.CheckConstraint(
                "patch_strategy IN ('append_rules','replace_summary','no_op')",
                name="ck_agent_evolution_drafts_patch_strategy",
            ),
        )

    draft_indexes = _indexes("agent_evolution_drafts")
    if "ix_agent_evolution_drafts_agent_status" not in draft_indexes:
        op.create_index(
            "ix_agent_evolution_drafts_agent_status",
            "agent_evolution_drafts",
            ["agent_id", "status"],
        )
    if "ix_agent_evolution_drafts_tenant_id" not in draft_indexes:
        op.create_index(
            "ix_agent_evolution_drafts_tenant_id",
            "agent_evolution_drafts",
            ["tenant_id"],
        )

    if "agent_harness_fixtures" not in tables:
        op.create_table(
            "agent_harness_fixtures",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("agent_template_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("fixture_role", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="role_qa"),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("task_summary", sa.Text(), nullable=False),
            sa.Column("acceptance_text", sa.Text(), nullable=True),
            sa.Column(
                "expected_keywords",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("rubric", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("weight", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_agent_harness_fixtures_tenant_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_agent_harness_fixtures_agent_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_template_id"],
                ["agent_templates.id"],
                name="fk_agent_harness_fixtures_agent_template_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_harness_fixtures"),
            sa.CheckConstraint(
                "kind IN ('role_skill','role_qa','role_style','custom')",
                name="ck_agent_harness_fixtures_kind",
            ),
        )

    fixture_indexes = _indexes("agent_harness_fixtures")
    if "ix_agent_harness_fixtures_agent_role" not in fixture_indexes:
        op.create_index(
            "ix_agent_harness_fixtures_agent_role",
            "agent_harness_fixtures",
            ["agent_id", "fixture_role"],
        )
    if "ix_agent_harness_fixtures_tenant_id" not in fixture_indexes:
        op.create_index(
            "ix_agent_harness_fixtures_tenant_id",
            "agent_harness_fixtures",
            ["tenant_id"],
        )

    if "agent_harness_runs" not in tables:
        op.create_table(
            "agent_harness_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("stage", sa.String(length=16), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="running",
            ),
            sa.Column("average_score", sa.Integer(), nullable=True),
            sa.Column("passed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("fixture_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "per_fixture",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_agent_harness_runs_tenant_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_agent_harness_runs_agent_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["draft_id"],
                ["agent_evolution_drafts.id"],
                name="fk_agent_harness_runs_draft_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_harness_runs"),
            sa.CheckConstraint(
                "stage IN ('baseline','candidate')",
                name="ck_agent_harness_runs_stage",
            ),
            sa.CheckConstraint(
                "status IN ('running','succeeded','failed','cancelled')",
                name="ck_agent_harness_runs_status",
            ),
        )

    run_indexes = _indexes("agent_harness_runs")
    if "ix_agent_harness_runs_draft_stage" not in run_indexes:
        op.create_index(
            "ix_agent_harness_runs_draft_stage",
            "agent_harness_runs",
            ["draft_id", "stage"],
        )
    if "ix_agent_harness_runs_tenant_id" not in run_indexes:
        op.create_index(
            "ix_agent_harness_runs_tenant_id",
            "agent_harness_runs",
            ["tenant_id"],
        )


def downgrade() -> None:
    tables = _tables()

    run_indexes = _indexes("agent_harness_runs")
    for idx in ("ix_agent_harness_runs_tenant_id", "ix_agent_harness_runs_draft_stage"):
        if idx in run_indexes:
            op.drop_index(idx, table_name="agent_harness_runs")
    if "agent_harness_runs" in tables:
        op.drop_table("agent_harness_runs")

    fixture_indexes = _indexes("agent_harness_fixtures")
    for idx in ("ix_agent_harness_fixtures_tenant_id", "ix_agent_harness_fixtures_agent_role"):
        if idx in fixture_indexes:
            op.drop_index(idx, table_name="agent_harness_fixtures")
    if "agent_harness_fixtures" in tables:
        op.drop_table("agent_harness_fixtures")

    draft_indexes = _indexes("agent_evolution_drafts")
    for idx in ("ix_agent_evolution_drafts_tenant_id", "ix_agent_evolution_drafts_agent_status"):
        if idx in draft_indexes:
            op.drop_index(idx, table_name="agent_evolution_drafts")
    if "agent_evolution_drafts" in tables:
        op.drop_table("agent_evolution_drafts")

    signal_indexes = _indexes("agent_evolution_signals")
    for idx in ("ix_agent_evolution_signals_tenant_id", "ix_agent_evolution_signals_agent_created"):
        if idx in signal_indexes:
            op.drop_index(idx, table_name="agent_evolution_signals")
    if "agent_evolution_signals" in tables:
        op.drop_table("agent_evolution_signals")