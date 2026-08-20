"""Create drafts table for Intent-Driven Orchestrator.

Revision ID: g001_create_drafts
Revises: f061_enterprise_info_tenant_id
Create Date: 2026-08-21 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "g001_create_drafts"
down_revision: Union[str, None] = "f061_enterprise_info_tenant_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "drafts" in inspector.get_table_names():
        return
    op.create_table(
        "drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_message", sa.Text, nullable=False),
        sa.Column("intent_summary", postgresql.JSONB, nullable=True),
        sa.Column("draft_payload", postgresql.JSONB, nullable=False),
        sa.Column("template_visibility", sa.String(32), nullable=False, server_default="user_private"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_detail", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_drafts_tenant_id", "drafts", ["tenant_id"])
    op.create_index("ix_drafts_user_id", "drafts", ["user_id"])
    op.create_index("ix_drafts_status", "drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_drafts_status", table_name="drafts")
    op.drop_index("ix_drafts_user_id", table_name="drafts")
    op.drop_index("ix_drafts_tenant_id", table_name="drafts")
    op.drop_table("drafts")
