"""Add persistent task dependencies and leader-confirmed workflow changes.

Revision ID: group_workflow_task_dag
Revises: scope_legacy_templates
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "group_workflow_task_dag"
down_revision: str | None = "scope_legacy_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    return index_name in {index["name"] for index in _inspector().get_indexes(table_name)}


def _has_check(table_name: str, constraint_name: str) -> bool:
    return constraint_name in {constraint["name"] for constraint in _inspector().get_check_constraints(table_name)}


def upgrade() -> None:
    if _has_table("group_workflow_items"):
        additions = (
            ("acceptance_criteria", postgresql.JSONB(), False, sa.text("'[]'::jsonb")),
            ("started_at", sa.DateTime(timezone=True), True, None),
            ("completed_at", sa.DateTime(timezone=True), True, None),
            ("failed_at", sa.DateTime(timezone=True), True, None),
            ("failure_code", sa.String(length=120), True, None),
            ("failure_summary", sa.Text(), True, None),
        )
        for name, column_type, nullable, server_default in additions:
            if not _has_column("group_workflow_items", name):
                op.add_column(
                    "group_workflow_items",
                    sa.Column(name, column_type, nullable=nullable, server_default=server_default),
                )
        if _has_check("group_workflow_items", "ck_group_workflow_items_status"):
            op.drop_constraint("ck_group_workflow_items_status", "group_workflow_items", type_="check")
        op.create_check_constraint(
            "ck_group_workflow_items_status",
            "group_workflow_items",
            "status IN ('pending', 'ready', 'in_progress', 'blocked', 'awaiting_approval', 'done', 'failed')",
        )

    if not _has_table("group_workflow_task_dependencies"):
        op.create_table(
            "group_workflow_task_dependencies",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workflow_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("group_workflows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "predecessor_item_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("group_workflow_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "successor_item_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("group_workflow_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "predecessor_item_id <> successor_item_id",
                name="ck_group_workflow_task_dependencies_distinct",
            ),
            sa.UniqueConstraint(
                "predecessor_item_id",
                "successor_item_id",
                name="uq_group_workflow_task_dependencies_edge",
            ),
        )
    for index_name, columns in (
        ("ix_group_workflow_task_dependencies_predecessor", ["predecessor_item_id"]),
        ("ix_group_workflow_task_dependencies_successor", ["successor_item_id"]),
    ):
        if _has_table("group_workflow_task_dependencies") and not _has_index(
            "group_workflow_task_dependencies", index_name
        ):
            op.create_index(index_name, "group_workflow_task_dependencies", columns)

    if not _has_table("group_workflow_change_requests"):
        op.create_table(
            "group_workflow_change_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workflow_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("group_workflows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "target_item_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("group_workflow_items.id", ondelete="SET NULL"),
            ),
            sa.Column(
                "requester_participant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("participants.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "confirmer_participant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("participants.id", ondelete="SET NULL"),
            ),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("before", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("after", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("impact", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True)),
            sa.Column("rejected_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "kind IN ('add', 'split', 'reconnect', 'acceptance')",
                name="ck_group_workflow_change_requests_kind",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'confirmed', 'rejected')",
                name="ck_group_workflow_change_requests_status",
            ),
        )
    if _has_table("group_workflow_change_requests") and not _has_index(
        "group_workflow_change_requests", "ix_group_workflow_change_requests_workflow_status"
    ):
        op.create_index(
            "ix_group_workflow_change_requests_workflow_status",
            "group_workflow_change_requests",
            ["workflow_id", "status"],
        )


def downgrade() -> None:
    if _has_table("group_workflow_change_requests"):
        if _has_index("group_workflow_change_requests", "ix_group_workflow_change_requests_workflow_status"):
            op.drop_index("ix_group_workflow_change_requests_workflow_status", table_name="group_workflow_change_requests")
        op.drop_table("group_workflow_change_requests")
    if _has_table("group_workflow_task_dependencies"):
        for index_name in (
            "ix_group_workflow_task_dependencies_successor",
            "ix_group_workflow_task_dependencies_predecessor",
        ):
            if _has_index("group_workflow_task_dependencies", index_name):
                op.drop_index(index_name, table_name="group_workflow_task_dependencies")
        op.drop_table("group_workflow_task_dependencies")
    if _has_table("group_workflow_items"):
        op.execute("UPDATE group_workflow_items SET status = 'pending' WHERE status = 'ready'")
        op.execute("UPDATE group_workflow_items SET status = 'blocked' WHERE status = 'failed'")
        if _has_check("group_workflow_items", "ck_group_workflow_items_status"):
            op.drop_constraint("ck_group_workflow_items_status", "group_workflow_items", type_="check")
        op.create_check_constraint(
            "ck_group_workflow_items_status",
            "group_workflow_items",
            "status IN ('pending', 'in_progress', 'blocked', 'awaiting_approval', 'done')",
        )
        for column_name in (
            "failure_summary",
            "failure_code",
            "failed_at",
            "completed_at",
            "started_at",
            "acceptance_criteria",
        ):
            if _has_column("group_workflow_items", column_name):
                op.drop_column("group_workflow_items", column_name)
