"""Assign legacy custom templates to the company of their creator.

Revision ID: scope_legacy_templates
Revises: agent_template_tenant
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "scope_legacy_templates"
down_revision: str | None = "agent_template_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_templates")}
    if "tenant_id" not in columns:
        return
    op.execute(
        """
        UPDATE agent_templates AS template
        SET tenant_id = creator.tenant_id
        FROM users AS creator
        WHERE template.created_by = creator.id
          AND template.is_builtin = false
          AND template.tenant_id IS NULL
          AND creator.tenant_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # The previous schema treated non-builtin templates as globally visible.
    op.execute("UPDATE agent_templates SET tenant_id = NULL WHERE is_builtin = false")
