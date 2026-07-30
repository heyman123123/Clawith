"""Store AI call start and finish times for monitoring pagination.

Revision ID: ai_interaction_times
Revises: ai_monitoring_logs
Create Date: 2026-07-30 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ai_interaction_times"
down_revision: str | None = "ai_monitoring_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("ai_interaction_logs")}
    if "started_at" not in columns:
        op.add_column("ai_interaction_logs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    if "finished_at" not in columns:
        op.add_column("ai_interaction_logs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE ai_interaction_logs SET started_at = created_at WHERE started_at IS NULL")
    op.execute("UPDATE ai_interaction_logs SET finished_at = created_at WHERE finished_at IS NULL")
    op.alter_column("ai_interaction_logs", "started_at", nullable=False)
    op.alter_column("ai_interaction_logs", "finished_at", nullable=False)
    indexes = {index["name"] for index in inspector.get_indexes("ai_interaction_logs")}
    if "ix_ai_interaction_logs_tenant_session_started" not in indexes:
        op.create_index(
            "ix_ai_interaction_logs_tenant_session_started",
            "ai_interaction_logs",
            ["tenant_id", "session_id", "started_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_ai_interaction_logs_tenant_session_started", table_name="ai_interaction_logs")
    op.drop_column("ai_interaction_logs", "finished_at")
    op.drop_column("ai_interaction_logs", "started_at")
