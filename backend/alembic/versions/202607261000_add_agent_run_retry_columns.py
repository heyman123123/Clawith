"""Add agent_runs retry columns.

Revision ID: add_agent_run_retry_cols
Revises: add_shareholder_groups
Create Date: 2026-07-26 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_agent_run_retry_cols"
down_revision: str | None = "add_shareholder_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    cols = _columns("agent_runs")
    if "retry_of_run_id" not in cols:
        op.add_column(
            "agent_runs",
            sa.Column("retry_of_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_agent_runs_retry_of_run_id",
            "agent_runs",
            "agent_runs",
            ["retry_of_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "retry_strategy" not in cols:
        op.add_column("agent_runs", sa.Column("retry_strategy", sa.String(32), nullable=True))
    if "failed_retryable" not in cols:
        op.add_column("agent_runs", sa.Column("failed_retryable", sa.Boolean(), nullable=True))
    if "ix_agent_runs_retry_of" not in _indexes("agent_runs"):
        op.create_index("ix_agent_runs_retry_of", "agent_runs", ["retry_of_run_id"])


def downgrade() -> None:
    indexes = _indexes("agent_runs")
    if "ix_agent_runs_retry_of" in indexes:
        op.drop_index("ix_agent_runs_retry_of", table_name="agent_runs")
    cols = _columns("agent_runs")
    if "retry_of_run_id" in cols:
        op.drop_constraint("fk_agent_runs_retry_of_run_id", "agent_runs", type_="foreignkey")
        op.drop_column("agent_runs", "retry_of_run_id")
    if "failed_retryable" in cols:
        op.drop_column("agent_runs", "failed_retryable")
    if "retry_strategy" in cols:
        op.drop_column("agent_runs", "retry_strategy")
