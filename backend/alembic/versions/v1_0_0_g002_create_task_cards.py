"""Create task_cards table.

Revision ID: g002_create_task_cards
Revises: g001_create_drafts
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g002_create_task_cards"
down_revision = "g001_create_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_cards" in inspector.get_table_names():
        return
    op.create_table(
        "task_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("okr_key_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("column", sa.String(32), nullable=False, server_default="backlog"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("priority", sa.String(16), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("target_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_paths", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_type", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_cards_tenant_id", "task_cards", ["tenant_id"])
    op.create_index("ix_task_cards_group_id", "task_cards", ["group_id"])
    op.create_index("ix_task_cards_assignee_agent_id", "task_cards", ["assignee_agent_id"])
    op.create_index("ix_task_cards_okr_key_result_id", "task_cards", ["okr_key_result_id"])
    op.create_index("ix_task_cards_tenant_group_column", "task_cards", ["tenant_id", "group_id", "column"])
    op.create_index("ix_task_cards_assignee_column", "task_cards", ["assignee_agent_id", "column"])


def downgrade() -> None:
    op.drop_index("ix_task_cards_assignee_column", table_name="task_cards")
    op.drop_index("ix_task_cards_tenant_group_column", table_name="task_cards")
    op.drop_index("ix_task_cards_okr_key_result_id", table_name="task_cards")
    op.drop_index("ix_task_cards_assignee_agent_id", table_name="task_cards")
    op.drop_index("ix_task_cards_group_id", table_name="task_cards")
    op.drop_index("ix_task_cards_tenant_id", table_name="task_cards")
    op.drop_table("task_cards")
