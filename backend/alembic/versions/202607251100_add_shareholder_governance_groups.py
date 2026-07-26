"""Add company-level shareholder governance groups and dispatch receipts.

Revision ID: add_shareholder_groups
Revises: add_project_decision_groups
Create Date: 2026-07-25 11:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_shareholder_groups"
down_revision: str | None = "add_project_decision_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "shareholder_groups" not in inspector.get_table_names():
        op.create_table(
            "shareholder_groups",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
            sa.Column("creator_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="RESTRICT"),
        )
        op.create_index("uq_shareholder_groups_tenant_id", "shareholder_groups", ["tenant_id"], unique=True)
    if "shareholder_dispatches" not in inspector.get_table_names():
        op.create_table(
            "shareholder_dispatches",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("shareholder_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("target_decision_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="dispatched"),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["shareholder_group_id"], ["shareholder_groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workflow_id"], ["project_workflows.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_decision_group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        )
        op.create_index("ix_shareholder_dispatches_group_created", "shareholder_dispatches", ["shareholder_group_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_shareholder_dispatches_group_created", table_name="shareholder_dispatches")
    op.drop_table("shareholder_dispatches")
    op.drop_index("uq_shareholder_groups_tenant_id", table_name="shareholder_groups")
    op.drop_table("shareholder_groups")
