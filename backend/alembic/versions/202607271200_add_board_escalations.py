"""Add board_escalations for decision-to-shareholder escalation.

Revision ID: add_board_escalations
Revises: add_project_milestones
Create Date: 2026-07-27 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_board_escalations"
down_revision: str | None = "add_project_milestones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "board_escalations" not in tables:
        op.create_table(
            "board_escalations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("decision_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("decision_session_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("shareholder_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("shareholder_session_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("escalation_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("board_resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["decision_group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["decision_session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["shareholder_group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["shareholder_session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workflow_id"], ["project_workflows.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = _indexes("board_escalations")
    if "ix_board_escalations_tenant_id" not in indexes:
        op.create_index("ix_board_escalations_tenant_id", "board_escalations", ["tenant_id"])
    if "ix_board_escalations_workflow_id" not in indexes:
        op.create_index("ix_board_escalations_workflow_id", "board_escalations", ["workflow_id"])
    if "ix_board_escalations_status" not in indexes:
        op.create_index("ix_board_escalations_status", "board_escalations", ["status"])
    if "uq_board_escalations_open_decision_session" not in indexes:
        op.create_index(
            "uq_board_escalations_open_decision_session",
            "board_escalations",
            ["decision_session_id", "status"],
            unique=True,
            postgresql_where=sa.text("status = 'open'"),
        )


def downgrade() -> None:
    indexes = _indexes("board_escalations")
    if "uq_board_escalations_open_decision_session" in indexes:
        op.drop_index("uq_board_escalations_open_decision_session", table_name="board_escalations")
    if "ix_board_escalations_status" in indexes:
        op.drop_index("ix_board_escalations_status", table_name="board_escalations")
    if "ix_board_escalations_workflow_id" in indexes:
        op.drop_index("ix_board_escalations_workflow_id", table_name="board_escalations")
    if "ix_board_escalations_tenant_id" in indexes:
        op.drop_index("ix_board_escalations_tenant_id", table_name="board_escalations")
    if "board_escalations" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("board_escalations")
