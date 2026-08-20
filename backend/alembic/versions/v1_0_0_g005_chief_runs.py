"""Create chief_runs table for the Persistent Orchestrator Chief Runtime.

Revision ID: g005_chief_runs
Revises: g004_task_board_events
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g005_chief_runs"
down_revision = "g004_task_board_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "chief_runs" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "chief_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chief_agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_thread_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_failure_reason", sa.Text, nullable=True),
        sa.UniqueConstraint("group_id", name="uq_chief_runs_group_id"),
    )
    op.create_index("ix_chief_runs_tenant_id", "chief_runs", ["tenant_id"])
    op.create_index("ix_chief_runs_group_id", "chief_runs", ["group_id"])
    op.create_index("ix_chief_runs_status", "chief_runs", ["status"])


def downgrade() -> None:
    op.drop_table("chief_runs")
