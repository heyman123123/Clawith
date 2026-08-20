"""Create task_board_events table.

Revision ID: g004_task_board_events
Revises: g003_task_card_deps
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g004_task_board_events"
down_revision = "g003_task_card_deps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "task_board_events" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "task_board_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_task_board_events_tenant_id", "task_board_events", ["tenant_id"])
    op.create_index("ix_task_board_events_group_id", "task_board_events", ["group_id"])
    op.create_index("ix_task_board_events_task_card_id", "task_board_events", ["task_card_id"])
    op.create_index(
        "ix_task_board_events_tenant_group_occurred",
        "task_board_events",
        ["tenant_id", "group_id", sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("task_board_events")
