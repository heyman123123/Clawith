"""Add tenant-scoped AI interaction monitoring records.

Revision ID: ai_monitoring_logs
Revises: team_builder_leader
Create Date: 2026-07-30 13:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ai_monitoring_logs"
down_revision: str | None = "team_builder_leader"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _create_indexes(existing_indexes: set[str]) -> None:
    indexes = (
        ("ix_ai_interaction_logs_tenant_created", ["tenant_id", "created_at"]),
        ("ix_ai_interaction_logs_tenant_status_created", ["tenant_id", "status", "created_at"]),
        ("ix_ai_interaction_logs_agent_created", ["agent_id", "created_at"]),
        ("ix_ai_interaction_logs_expires_at", ["expires_at"]),
    )
    for name, columns in indexes:
        if name not in existing_indexes:
            op.create_index(name, "ai_interaction_logs", columns)


def upgrade() -> None:
    inspector = _inspector()
    if not inspector.has_table("ai_interaction_logs"):
        op.create_table(
            "ai_interaction_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL")),
            sa.Column(
                "llm_model_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("llm_models.id", ondelete="SET NULL"),
            ),
            sa.Column("session_id", sa.String(length=255)),
            sa.Column("run_id", sa.String(length=255)),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("invocation_kind", sa.String(length=16), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model_name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("token_source", sa.String(length=16), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_ms", sa.Integer()),
            sa.Column("request_context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("response_content", sa.Text()),
            sa.Column("error", postgresql.JSONB()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("status IN ('success', 'error')", name="ck_ai_interaction_logs_status"),
            sa.CheckConstraint(
                "token_source IN ('provider', 'estimated', 'unavailable')",
                name="ck_ai_interaction_logs_token_source",
            ),
        )
        _create_indexes(set())
        return
    _create_indexes({index["name"] for index in inspector.get_indexes("ai_interaction_logs")})


def downgrade() -> None:
    if not _inspector().has_table("ai_interaction_logs"):
        return
    op.drop_index("ix_ai_interaction_logs_expires_at", table_name="ai_interaction_logs")
    op.drop_index("ix_ai_interaction_logs_agent_created", table_name="ai_interaction_logs")
    op.drop_index("ix_ai_interaction_logs_tenant_status_created", table_name="ai_interaction_logs")
    op.drop_index("ix_ai_interaction_logs_tenant_created", table_name="ai_interaction_logs")
    op.drop_table("ai_interaction_logs")
