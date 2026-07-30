"""Add group_run_resume_jobs for failed Run leader notify and model probes.

Revision ID: group_run_resume_jobs
Revises: group_decision_maker
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "group_run_resume_jobs"
down_revision: str | None = "group_decision_maker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("group_run_resume_jobs"):
        return
    op.create_table(
        "group_run_resume_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", name="fk_group_run_resume_jobs_tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", name="fk_group_run_resume_jobs_group_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", name="fk_group_run_resume_jobs_session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "failed_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", name="fk_group_run_resume_jobs_failed_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "failed_agent_participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "participants.id",
                name="fk_group_run_resume_jobs_failed_agent_participant_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("leader_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('general', 'model_quota')", name="ck_group_run_resume_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'notified', 'recovered_notified', 'timed_out', 'cancelled')",
            name="ck_group_run_resume_jobs_status",
        ),
        sa.UniqueConstraint("failed_run_id", name="uq_group_run_resume_jobs_failed_run_id"),
    )
    op.create_index(
        "ix_group_run_resume_jobs_pending_check",
        "group_run_resume_jobs",
        ["status", "next_check_at"],
    )
    op.create_index(
        "ix_group_run_resume_jobs_group",
        "group_run_resume_jobs",
        ["group_id", "created_at"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("group_run_resume_jobs"):
        return
    op.drop_index("ix_group_run_resume_jobs_group", table_name="group_run_resume_jobs")
    op.drop_index("ix_group_run_resume_jobs_pending_check", table_name="group_run_resume_jobs")
    op.drop_table("group_run_resume_jobs")
