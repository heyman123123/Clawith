"""Add project milestones and task milestone_id.

Revision ID: add_project_milestones
Revises: add_hr_review_board
Create Date: 2026-07-26 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_project_milestones"
down_revision: str | None = "add_hr_review_board"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if foreign_key.get("name")
    }


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    if "project_milestones" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "project_milestones",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("order_index", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("created_by_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["project_workflows.id"],
                name="fk_project_milestones_workflow_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["created_by_agent_id"],
                ["agents.id"],
                name="fk_project_milestones_created_by_agent_id_agents",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_project_milestones"),
        )
    milestone_indexes = _indexes("project_milestones")
    if "ix_project_milestones_workflow_order" not in milestone_indexes:
        op.create_index(
            "ix_project_milestones_workflow_order",
            "project_milestones",
            ["workflow_id", "order_index"],
        )

    task_columns = _columns("tasks")
    if "milestone_id" not in task_columns:
        op.add_column("tasks", sa.Column("milestone_id", postgresql.UUID(as_uuid=True), nullable=True))
    task_foreign_keys = _foreign_keys("tasks")
    if "fk_tasks_milestone_id_project_milestones" not in task_foreign_keys:
        op.create_foreign_key(
            "fk_tasks_milestone_id_project_milestones",
            "tasks",
            "project_milestones",
            ["milestone_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if _dialect_name() == "postgresql":
        op.execute(
            "COMMENT ON COLUMN tasks.due_date IS "
            "'DEPRECATED: 不再使用，进度推进完全基于依赖 DAG'"
        )
        op.execute(
            "COMMENT ON COLUMN tasks.remind_schedule IS "
            "'DEPRECATED: 不再使用，进度推进完全基于依赖 DAG'"
        )


def downgrade() -> None:
    task_foreign_keys = _foreign_keys("tasks")
    if "fk_tasks_milestone_id_project_milestones" in task_foreign_keys:
        op.drop_constraint("fk_tasks_milestone_id_project_milestones", "tasks", type_="foreignkey")
    if "milestone_id" in _columns("tasks"):
        op.drop_column("tasks", "milestone_id")

    milestone_indexes = _indexes("project_milestones")
    if "ix_project_milestones_workflow_order" in milestone_indexes:
        op.drop_index("ix_project_milestones_workflow_order", table_name="project_milestones")
    if "project_milestones" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("project_milestones")
