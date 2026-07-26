"""Add governance role pools, decision records, and agent reusable flag.

Revision ID: add_governance_role_pools
Revises: add_agent_run_retry_cols
Create Date: 2026-07-26 11:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_governance_role_pools"
down_revision: str | None = "add_agent_run_retry_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    agent_cols = _columns("agents")
    if "reusable" not in agent_cols:
        op.add_column(
            "agents",
            sa.Column("reusable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    if "governance_role_pools" not in tables:
        op.create_table(
            "governance_role_pools",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("role_type", sa.String(length=32), nullable=False),
            sa.Column("role_key", sa.String(length=64), nullable=False),
            sa.Column("role_title", sa.String(length=100), nullable=False),
            sa.Column("is_default_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "role_key", name="uq_governance_role_pools_tenant_role_key"),
        )
    pool_indexes = _indexes("governance_role_pools") if "governance_role_pools" in tables or True else set()
    if "ix_governance_role_pools_tenant_id" not in pool_indexes:
        op.create_index("ix_governance_role_pools_tenant_id", "governance_role_pools", ["tenant_id"])

    if "decision_records" not in tables:
        op.create_table(
            "decision_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("decision_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("decision_session_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_session_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("decision_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("participants", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="dispatched"),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["workflow_id"], ["project_workflows.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["decision_group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["decision_session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        )
    record_indexes = _indexes("decision_records") if "decision_records" in tables or True else set()
    if "ix_decision_records_workflow_id" not in record_indexes:
        op.create_index("ix_decision_records_workflow_id", "decision_records", ["workflow_id"])
    if "ix_decision_records_status" not in record_indexes:
        op.create_index("ix_decision_records_status", "decision_records", ["status"])


def downgrade() -> None:
    if "ix_decision_records_status" in _indexes("decision_records"):
        op.drop_index("ix_decision_records_status", table_name="decision_records")
    if "ix_decision_records_workflow_id" in _indexes("decision_records"):
        op.drop_index("ix_decision_records_workflow_id", table_name="decision_records")
    inspector = sa.inspect(op.get_bind())
    if "decision_records" in inspector.get_table_names():
        op.drop_table("decision_records")

    if "ix_governance_role_pools_tenant_id" in _indexes("governance_role_pools"):
        op.drop_index("ix_governance_role_pools_tenant_id", table_name="governance_role_pools")
    if "governance_role_pools" in inspector.get_table_names():
        op.drop_table("governance_role_pools")

    if "reusable" in _columns("agents"):
        op.drop_column("agents", "reusable")
