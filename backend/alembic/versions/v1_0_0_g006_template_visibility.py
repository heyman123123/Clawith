"""Extend agent_templates with template_visibility / approval fields.

Revision ID: g006_template_visibility
Revises: g005_chief_runs

Idempotent: Safe for retry; uses sa.inspect to check column existence.
Note: existing created_by column (UUID) is reused as created_by_user_id.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g006_template_visibility"
down_revision = "g005_chief_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("agent_templates")}

    if "template_visibility" not in existing:
        op.add_column(
            "agent_templates",
            sa.Column("template_visibility", sa.String(32), nullable=False, server_default="public"),
        )
    if "tenant_id" not in existing:
        op.add_column("agent_templates", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    if "approval_state" not in existing:
        op.add_column(
            "agent_templates",
            sa.Column("approval_state", sa.String(32), nullable=False, server_default="approved"),
        )
    if "rejected_reason" not in existing:
        op.add_column("agent_templates", sa.Column("rejected_reason", sa.Text, nullable=True))

    indexes = {i["name"] for i in inspector.get_indexes("agent_templates")}
    if "ix_agent_templates_visibility" not in indexes:
        op.create_index("ix_agent_templates_visibility", "agent_templates", ["template_visibility"])
    if "ix_agent_templates_tenant_visibility" not in indexes:
        op.create_index(
            "ix_agent_templates_tenant_visibility",
            "agent_templates",
            ["tenant_id", "template_visibility"],
        )


def downgrade() -> None:
    op.drop_index("ix_agent_templates_tenant_visibility", table_name="agent_templates")
    op.drop_index("ix_agent_templates_visibility", table_name="agent_templates")
    op.drop_column("agent_templates", "rejected_reason")
    op.drop_column("agent_templates", "approval_state")
    op.drop_column("agent_templates", "tenant_id")
    op.drop_column("agent_templates", "template_visibility")
