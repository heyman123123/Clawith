"""Add chief_run_id to chat_sessions for Chief 1:1 Chat.

Revision ID: g007_chief_run_id
Revises: g006_template_visibility

Idempotent: Safe for retry; uses sa.inspect to check column existence.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g007_chief_run_id"
down_revision = "g006_template_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("chat_sessions")}
    if "chief_run_id" not in existing:
        op.add_column(
            "chat_sessions",
            sa.Column("chief_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    indexes = {i["name"] for i in inspector.get_indexes("chat_sessions")}
    if "ix_chat_sessions_chief_run_id" not in indexes:
        op.create_index("ix_chat_sessions_chief_run_id", "chat_sessions", ["chief_run_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_chief_run_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "chief_run_id")
