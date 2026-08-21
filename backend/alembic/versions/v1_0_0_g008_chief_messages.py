"""Create chief_messages table for the ChiefChat feature.

Stores user/agent messages exchanged with a Chief Runtime in 1:1 PM chats.
Separate from chat_messages so the chief flow is independent of regular
Agent chats.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g008_chief_messages"
down_revision = "g007_chief_run_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "chief_messages" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "chief_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chief_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # 'user' | 'chief'
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chief_messages_tenant_id", "chief_messages", ["tenant_id"])
    op.create_index("ix_chief_messages_chief_run_id", "chief_messages", ["chief_run_id"])
    op.create_index("ix_chief_messages_chief_run_id_created_at", "chief_messages", ["chief_run_id", "created_at"])


def downgrade() -> None:
    op.drop_table("chief_messages")
