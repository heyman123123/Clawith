"""Create task_card_dependencies table.

Revision ID: g003_task_card_deps
Revises: g002_create_task_cards
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g003_task_card_deps"
down_revision = "g002_create_task_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "task_card_dependencies" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "task_card_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("depends_on_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dependency_type", sa.String(16), nullable=False, server_default="blocks"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_card_id", "depends_on_card_id", name="uq_task_card_deps_pair"),
    )
    op.create_index("ix_task_card_dependencies_tenant_id", "task_card_dependencies", ["tenant_id"])
    op.create_index("ix_task_card_dependencies_task_card_id", "task_card_dependencies", ["task_card_id"])
    op.create_index("ix_task_card_dependencies_depends_on_card", "task_card_dependencies", ["depends_on_card_id"])


def downgrade() -> None:
    op.drop_table("task_card_dependencies")
