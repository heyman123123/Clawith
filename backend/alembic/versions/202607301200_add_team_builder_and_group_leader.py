"""Add durable intelligent-team drafts, jobs, and the group leader identity.

Revision ID: team_builder_leader
Revises: allow_checkpoint_deliveries
Create Date: 2026-07-30 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# `alembic_version.version_num` in historical installations is VARCHAR(32).
revision: str = "team_builder_leader"
down_revision: str | None = "allow_checkpoint_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    """Use a fresh inspector because this revision may repair a partial prior run."""
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    return index_name in {index["name"] for index in _inspector().get_indexes(table_name)}


def _has_foreign_key(
    table_name: str, constrained_column: str, referred_table: str, referred_column: str
) -> bool:
    return any(
        foreign_key.get("constrained_columns") == [constrained_column]
        and foreign_key.get("referred_table") == referred_table
        and foreign_key.get("referred_columns") == [referred_column]
        for foreign_key in _inspector().get_foreign_keys(table_name)
    )


def _create_team_build_drafts() -> None:
    op.create_table(
        "team_build_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "creator_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("generated_plan", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_plan", postgresql.JSONB(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_plan_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="generating"),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('generating', 'ready', 'invalid', 'confirmed', 'expired', 'cancelled')",
            name="ck_team_build_drafts_status",
        ),
    )


def _create_team_provision_jobs() -> None:
    op.create_table(
        "team_provision_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_build_drafts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requesting_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column(
            "leader_participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "activation_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'validating', 'provisioning_agents', 'waiting_for_agents', 'creating_group', 'activating', 'completed', 'retryable_failed', 'failed')",
            name="ck_team_provision_jobs_status",
        ),
        sa.UniqueConstraint("draft_id", "idempotency_key", name="uq_team_provision_jobs_draft_idempotency"),
    )


def _create_team_provision_members() -> None:
    op.create_table(
        "team_provision_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_provision_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_key", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("role_spec", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column(
            "participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('existing', 'new')", name="ck_team_provision_members_source"),
        sa.CheckConstraint(
            "status IN ('pending', 'resolving', 'creating', 'waiting', 'ready', 'failed')",
            name="ck_team_provision_members_status",
        ),
        sa.UniqueConstraint("job_id", "member_key", name="uq_team_provision_members_job_key"),
    )


def upgrade() -> None:
    """Apply safely when an interrupted or metadata-created schema already has some objects."""
    if not _has_column("groups", "leader_participant_id"):
        op.add_column("groups", sa.Column("leader_participant_id", postgresql.UUID(as_uuid=True), nullable=True))
    if not _has_foreign_key("groups", "leader_participant_id", "participants", "id"):
        op.create_foreign_key(
            "fk_groups_leader_participant_id_participants",
            "groups",
            "participants",
            ["leader_participant_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    if not _has_table("team_build_drafts"):
        _create_team_build_drafts()
    if not _has_index("team_build_drafts", "ix_team_build_drafts_tenant_creator_updated"):
        op.create_index(
            "ix_team_build_drafts_tenant_creator_updated",
            "team_build_drafts",
            ["tenant_id", "creator_user_id", "updated_at"],
        )

    if not _has_table("team_provision_jobs"):
        _create_team_provision_jobs()
    if not _has_index("team_provision_jobs", "ix_team_provision_jobs_status_updated"):
        op.create_index("ix_team_provision_jobs_status_updated", "team_provision_jobs", ["status", "updated_at"])

    if not _has_table("team_provision_members"):
        _create_team_provision_members()
    if not _has_index("team_provision_members", "ix_team_provision_members_job_status"):
        op.create_index("ix_team_provision_members_job_status", "team_provision_members", ["job_id", "status"])


def downgrade() -> None:
    if _has_table("team_provision_members"):
        if _has_index("team_provision_members", "ix_team_provision_members_job_status"):
            op.drop_index("ix_team_provision_members_job_status", table_name="team_provision_members")
        op.drop_table("team_provision_members")
    if _has_table("team_provision_jobs"):
        if _has_index("team_provision_jobs", "ix_team_provision_jobs_status_updated"):
            op.drop_index("ix_team_provision_jobs_status_updated", table_name="team_provision_jobs")
        op.drop_table("team_provision_jobs")
    if _has_table("team_build_drafts"):
        if _has_index("team_build_drafts", "ix_team_build_drafts_tenant_creator_updated"):
            op.drop_index("ix_team_build_drafts_tenant_creator_updated", table_name="team_build_drafts")
        op.drop_table("team_build_drafts")
    if _has_foreign_key("groups", "leader_participant_id", "participants", "id"):
        op.drop_constraint("fk_groups_leader_participant_id_participants", "groups", type_="foreignkey")
    if _has_column("groups", "leader_participant_id"):
        op.drop_column("groups", "leader_participant_id")
