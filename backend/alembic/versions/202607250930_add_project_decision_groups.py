"""Add a governance group for each project workflow.

Revision ID: add_project_decision_groups
Revises: add_project_decisions
Create Date: 2026-07-25 09:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_project_decision_groups"
down_revision: str | None = "add_project_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    workflow_columns = {column["name"] for column in inspector.get_columns("project_workflows")}
    if "decision_group_id" not in workflow_columns:
        op.add_column(
            "project_workflows",
            sa.Column("decision_group_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_project_workflows_decision_group_id_groups",
            "project_workflows",
            "groups",
            ["decision_group_id"],
            ["id"],
        )

    decision_columns = {column["name"] for column in inspector.get_columns("project_decisions")}
    if "review_group_id" not in decision_columns:
        op.add_column(
            "project_decisions",
            sa.Column("review_group_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_project_decisions_review_group_id_groups",
            "project_decisions",
            "groups",
            ["review_group_id"],
            ["id"],
            ondelete="CASCADE",
        )
    indexes = {index["name"] for index in inspector.get_indexes("project_decisions")}
    if "ix_project_decisions_review_group_status" not in indexes:
        op.create_index(
            "ix_project_decisions_review_group_status",
            "project_decisions",
            ["review_group_id", "status", "created_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_project_decisions_review_group_status", table_name="project_decisions")
    op.drop_constraint("fk_project_decisions_review_group_id_groups", "project_decisions", type_="foreignkey")
    op.drop_column("project_decisions", "review_group_id")
    op.drop_constraint("fk_project_workflows_decision_group_id_groups", "project_workflows", type_="foreignkey")
    op.drop_column("project_workflows", "decision_group_id")
