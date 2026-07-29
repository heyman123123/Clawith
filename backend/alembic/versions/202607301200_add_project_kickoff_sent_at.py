"""Add kickoff_sent_at to project_workflows.

Revision ID: add_project_kickoff_sent_at
Revises: add_board_escalations
Create Date: 2026-07-30 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_project_kickoff_sent_at"
down_revision: str | None = "add_board_escalations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "project_workflows" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "kickoff_sent_at" not in _columns("project_workflows"):
        op.add_column(
            "project_workflows",
            sa.Column("kickoff_sent_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if "project_workflows" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "kickoff_sent_at" in _columns("project_workflows"):
        op.drop_column("project_workflows", "kickoff_sent_at")
