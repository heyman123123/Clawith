"""Add agent role versions and evolution records (P3).

Revision ID: add_evolution_records
Revises: add_ao_workflow_runs
Create Date: 2026-07-27 20:00:00

The evolution engine (P3) needs durable history for every meaningful soul /
prompt change to an Agent so the quality engine can:

* Look up which prompt version produced which verdict (regression harness).
* Roll back to the previous soul with one call when a new prompt regresses.

This migration adds two tables:

* ``agent_role_versions`` — append-only history of soul changes per Agent
  (or per AgentTemplate when the change is at the template level). Each row
  captures ``version_no``, ``soul_md``, the verdict/score that triggered the
  change (when applicable) and the operator (system vs human).

* ``agent_evolution_records`` — one row per "evolution event" (initial
  baseline, evolution applied, rollback, manual override). Stores the
  reason, source version, target version and the verdict context that
  motivated the change.

This migration is strictly additive. Down migration drops the new tables
in reverse order; no existing tables are touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "add_evolution_records"
down_revision: str | None = "add_ao_workflow_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if "agent_role_versions" not in tables:
        op.create_table(
            "agent_role_versions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("agent_template_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("soul_md", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="baseline"),
            sa.Column("evolution_record_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("quality_score", sa.Integer(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_agent_role_versions_agent_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_template_id"],
                ["agent_templates.id"],
                name="fk_agent_role_versions_agent_template_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_agent_role_versions_tenant_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_role_versions"),
            sa.UniqueConstraint(
                "agent_id",
                "version_no",
                name="uq_agent_role_versions_agent_version",
            ),
            sa.UniqueConstraint(
                "agent_template_id",
                "version_no",
                name="uq_agent_role_versions_template_version",
            ),
            sa.CheckConstraint(
                "source IN ('baseline','evolution','rollback','manual')",
                name="ck_agent_role_versions_source",
            ),
        )

    role_version_indexes = _indexes("agent_role_versions")
    if "ix_agent_role_versions_agent_id" not in role_version_indexes:
        op.create_index(
            "ix_agent_role_versions_agent_id",
            "agent_role_versions",
            ["agent_id"],
        )
    if "ix_agent_role_versions_template_id" not in role_version_indexes:
        op.create_index(
            "ix_agent_role_versions_template_id",
            "agent_role_versions",
            ["agent_template_id"],
        )
    if "ix_agent_role_versions_tenant_id" not in role_version_indexes:
        op.create_index(
            "ix_agent_role_versions_tenant_id",
            "agent_role_versions",
            ["tenant_id"],
        )

    if "agent_evolution_records" not in tables:
        op.create_table(
            "agent_evolution_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("agent_template_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("trigger_source", sa.String(length=64), nullable=False),
            sa.Column("trigger_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("to_version_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("quality_score_before", sa.Integer(), nullable=True),
            sa.Column("quality_score_after", sa.Integer(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_agent_evolution_records_agent_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_template_id"],
                ["agent_templates.id"],
                name="fk_agent_evolution_records_agent_template_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["from_version_id"],
                ["agent_role_versions.id"],
                name="fk_agent_evolution_records_from_version",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["to_version_id"],
                ["agent_role_versions.id"],
                name="fk_agent_evolution_records_to_version",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_agent_evolution_records_tenant_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_agent_evolution_records"),
            sa.CheckConstraint(
                "kind IN ('baseline','evolution','rollback','manual')",
                name="ck_agent_evolution_records_kind",
            ),
        )

    evolution_indexes = _indexes("agent_evolution_records")
    if "ix_agent_evolution_records_agent_id" not in evolution_indexes:
        op.create_index(
            "ix_agent_evolution_records_agent_id",
            "agent_evolution_records",
            ["agent_id"],
        )
    if "ix_agent_evolution_records_tenant_id" not in evolution_indexes:
        op.create_index(
            "ix_agent_evolution_records_tenant_id",
            "agent_evolution_records",
            ["tenant_id"],
        )
    if "ix_agent_evolution_records_kind" not in evolution_indexes:
        op.create_index(
            "ix_agent_evolution_records_kind",
            "agent_evolution_records",
            ["kind"],
        )


def downgrade() -> None:
    evolution_indexes = _indexes("agent_evolution_records")
    for idx in (
        "ix_agent_evolution_records_kind",
        "ix_agent_evolution_records_tenant_id",
        "ix_agent_evolution_records_agent_id",
    ):
        if idx in evolution_indexes:
            op.drop_index(idx, table_name="agent_evolution_records")
    if "agent_evolution_records" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("agent_evolution_records")

    role_version_indexes = _indexes("agent_role_versions")
    for idx in (
        "ix_agent_role_versions_tenant_id",
        "ix_agent_role_versions_template_id",
        "ix_agent_role_versions_agent_id",
    ):
        if idx in role_version_indexes:
            op.drop_index(idx, table_name="agent_role_versions")
    if "agent_role_versions" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("agent_role_versions")