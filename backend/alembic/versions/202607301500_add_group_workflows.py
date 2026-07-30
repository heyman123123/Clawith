"""Add durable evidence-driven group workflows.

Revision ID: group_workflows
Revises: ai_interaction_times
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "group_workflows"
down_revision: str | None = "ai_interaction_times"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("leader_participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(length=200), nullable=False), sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("current_stage_id", postgresql.UUID(as_uuid=True)), sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('default', 'agile', 'product_research', 'ai')", name="ck_group_workflows_source"),
        sa.CheckConstraint("status IN ('active', 'paused', 'awaiting_approval', 'completed')", name="ck_group_workflows_status"),
        sa.UniqueConstraint("group_id", name="uq_group_workflows_group"),
    )
    op.create_table(
        "group_workflow_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("workflow_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_key", sa.String(length=80), nullable=False), sa.Column("title", sa.String(length=200), nullable=False), sa.Column("goal", sa.Text(), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"), sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("owner_participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending', 'active', 'awaiting_approval', 'completed', 'blocked')", name="ck_group_workflow_stages_status"),
        sa.UniqueConstraint("workflow_id", "position", name="uq_group_workflow_stages_position"), sa.UniqueConstraint("workflow_id", "stage_key", name="uq_group_workflow_stages_key"),
    )
    op.create_table(
        "group_workflow_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("workflow_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_workflows.id", ondelete="CASCADE"), nullable=False), sa.Column("stage_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_workflow_stages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(length=100), nullable=False), sa.Column("title", sa.String(length=300), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("assignee_participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"), sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("blocked_reason", sa.Text()), sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'blocked', 'awaiting_approval', 'done')", name="ck_group_workflow_items_status"), sa.UniqueConstraint("stage_id", "item_key", name="uq_group_workflow_items_key"),
    )
    op.create_table(
        "group_workflow_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("workflow_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_workflows.id", ondelete="CASCADE"), nullable=False), sa.Column("stage_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_workflow_stages.id", ondelete="SET NULL")), sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_workflow_items.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(length=64), nullable=False), sa.Column("actor_participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL")), sa.Column("source", sa.String(length=32), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("dispatch_state", sa.String(length=16), nullable=False, server_default="none"), sa.Column("dispatched_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("dispatch_state IN ('none', 'pending', 'claimed', 'dispatched')", name="ck_group_workflow_events_dispatch"), sa.UniqueConstraint("workflow_id", "idempotency_key", name="uq_group_workflow_events_idempotency"),
    )
    op.create_table(
        "group_workflow_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False), sa.Column("creator_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("plan", postgresql.JSONB()), sa.Column("status", sa.String(length=24), nullable=False, server_default="generating"), sa.Column("error_code", sa.String(length=100)), sa.Column("error_message", sa.Text()), sa.Column("confirmed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('generating', 'ready', 'failed', 'confirmed', 'cancelled')", name="ck_group_workflow_drafts_status"),
    )
    op.create_index("ix_group_workflows_tenant_status", "group_workflows", ["tenant_id", "status"])
    op.create_index("ix_group_workflow_stages_workflow_status", "group_workflow_stages", ["workflow_id", "status"])
    op.create_index("ix_group_workflow_items_stage_status", "group_workflow_items", ["stage_id", "status"])
    op.create_index("ix_group_workflow_items_assignee_status", "group_workflow_items", ["assignee_participant_id", "status"])
    op.create_index("ix_group_workflow_events_workflow_created", "group_workflow_events", ["workflow_id", "created_at"])
    op.create_index("ix_group_workflow_events_dispatch", "group_workflow_events", ["dispatch_state", "created_at"])
    op.create_index("ix_group_workflow_drafts_group_created", "group_workflow_drafts", ["group_id", "created_at"])


def downgrade() -> None:
    for table, index in (("group_workflow_drafts", "ix_group_workflow_drafts_group_created"), ("group_workflow_events", "ix_group_workflow_events_dispatch"), ("group_workflow_events", "ix_group_workflow_events_workflow_created"), ("group_workflow_items", "ix_group_workflow_items_assignee_status"), ("group_workflow_items", "ix_group_workflow_items_stage_status"), ("group_workflow_stages", "ix_group_workflow_stages_workflow_status"), ("group_workflows", "ix_group_workflows_tenant_status")):
        op.drop_index(index, table_name=table)
    op.drop_table("group_workflow_drafts")
    op.drop_table("group_workflow_events")
    op.drop_table("group_workflow_items")
    op.drop_table("group_workflow_stages")
    op.drop_table("group_workflows")
