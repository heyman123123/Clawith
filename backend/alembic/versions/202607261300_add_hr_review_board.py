"""Add HR review board schema.

Revision ID: add_hr_review_board
Revises: add_governance_role_pools
Create Date: 2026-07-26 13:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_hr_review_board"
down_revision: str | None = "add_governance_role_pools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    group_cols = _columns("groups")
    if "group_type" not in group_cols:
        op.add_column("groups", sa.Column("group_type", sa.String(length=64), nullable=True))

    session_cols = _columns("chat_sessions")
    if "parent_session_id" not in session_cols:
        op.add_column(
            "chat_sessions",
            sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_chat_sessions_parent_session_id_chat_sessions",
            "chat_sessions",
            "chat_sessions",
            ["parent_session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    inspector = sa.inspect(op.get_bind())
    if "hr_review_sessions" not in inspector.get_table_names():
        op.create_table(
            "hr_review_sessions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("session_type", sa.String(length=32), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="open",
            ),
            sa.Column(
                "proposals",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("selected_proposal_id", sa.String(length=64), nullable=True),
            sa.Column(
                "context_payload",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "session_type IN ('team_building', 'governance_topup')",
                name="ck_hr_review_sessions_session_type",
            ),
            sa.CheckConstraint(
                "status IN ('open', 'user_selected', 'completed')",
                name="ck_hr_review_sessions_status",
            ),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        )
    hr_indexes = _indexes("hr_review_sessions") if "hr_review_sessions" in inspector.get_table_names() else set()
    if "ix_hr_review_sessions_group_id" not in hr_indexes:
        op.create_index("ix_hr_review_sessions_group_id", "hr_review_sessions", ["group_id"])
    if "ix_hr_review_sessions_session_id" not in hr_indexes:
        op.create_index("ix_hr_review_sessions_session_id", "hr_review_sessions", ["session_id"])


def downgrade() -> None:
    if "hr_review_sessions" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_hr_review_sessions_session_id", table_name="hr_review_sessions")
        op.drop_index("ix_hr_review_sessions_group_id", table_name="hr_review_sessions")
        op.drop_table("hr_review_sessions")

    session_cols = _columns("chat_sessions")
    if "parent_session_id" in session_cols:
        op.drop_constraint(
            "fk_chat_sessions_parent_session_id_chat_sessions",
            "chat_sessions",
            type_="foreignkey",
        )
        op.drop_column("chat_sessions", "parent_session_id")

    group_cols = _columns("groups")
    if "group_type" in group_cols:
        op.drop_column("groups", "group_type")
