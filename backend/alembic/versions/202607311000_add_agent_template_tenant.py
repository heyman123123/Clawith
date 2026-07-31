"""Scope company-created agent templates to their tenant.

Revision ID: agent_template_tenant
Revises: group_run_resume_jobs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "agent_template_tenant"
down_revision: str | None = "group_run_resume_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_templates")}
    if "tenant_id" not in columns:
        op.add_column("agent_templates", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_agent_templates_tenant_id_tenants",
            "agent_templates",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_agent_templates_tenant_id", "agent_templates", ["tenant_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_templates")}
    if "tenant_id" in columns:
        indexes = {index["name"] for index in inspector.get_indexes("agent_templates")}
        if "ix_agent_templates_tenant_id" in indexes:
            op.drop_index("ix_agent_templates_tenant_id", table_name="agent_templates")
        op.drop_constraint("fk_agent_templates_tenant_id_tenants", "agent_templates", type_="foreignkey")
        op.drop_column("agent_templates", "tenant_id")
