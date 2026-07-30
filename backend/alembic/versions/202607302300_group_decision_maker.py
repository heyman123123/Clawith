"""Add group decision maker fields and decision requests.

Revision ID: group_decision_maker
Revises: okr_push_cadence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "group_decision_maker"
down_revision: str | None = "okr_push_cadence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    group_cols = {column["name"] for column in inspector.get_columns("groups")}
    if "decision_maker_participant_id" not in group_cols:
        op.add_column(
            "groups",
            sa.Column("decision_maker_participant_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_groups_decision_maker_participant_id_participants",
            "groups",
            "participants",
            ["decision_maker_participant_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    if "decision_report_participant_ids" not in group_cols:
        op.add_column(
            "groups",
            sa.Column("decision_report_participant_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )

    if not inspector.has_table("group_decision_requests"):
        op.create_table(
            "group_decision_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", name="fk_group_decision_requests_tenant_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "group_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("groups.id", name="fk_group_decision_requests_group_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "workflow_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "group_workflows.id",
                    name="fk_group_decision_requests_workflow_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
            sa.Column(
                "stage_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "group_workflow_stages.id",
                    name="fk_group_decision_requests_stage_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
            sa.Column(
                "decision_maker_participant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "participants.id",
                    name="fk_group_decision_requests_decision_maker_participant_id",
                    ondelete="RESTRICT",
                ),
                nullable=False,
            ),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("recommendation", sa.Text(), nullable=True),
            sa.Column("options_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="pending_owner_confirm",
            ),
            sa.Column(
                "approver_participant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "participants.id",
                    name="fk_group_decision_requests_approver_participant_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("report_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.CheckConstraint(
                "category IN ('routine', 'human_comms', 'external_deploy', 'finance', 'uncertain')",
                name="ck_group_decision_requests_category",
            ),
            sa.CheckConstraint(
                "status IN ('pending_owner_confirm', 'approved', 'rejected', 'auto_applied', 'cancelled')",
                name="ck_group_decision_requests_status",
            ),
        )
        op.create_index(
            "ix_group_decision_requests_group_status",
            "group_decision_requests",
            ["group_id", "status"],
        )
        op.create_index(
            "ix_group_decision_requests_tenant_created",
            "group_decision_requests",
            ["tenant_id", "created_at"],
        )


def downgrade() -> None:
    inspector = _inspector()
    if inspector.has_table("group_decision_requests"):
        op.drop_index("ix_group_decision_requests_tenant_created", table_name="group_decision_requests")
        op.drop_index("ix_group_decision_requests_group_status", table_name="group_decision_requests")
        op.drop_table("group_decision_requests")

    group_cols = {column["name"] for column in inspector.get_columns("groups")}
    if "decision_report_participant_ids" in group_cols:
        op.drop_column("groups", "decision_report_participant_ids")
    if "decision_maker_participant_id" in group_cols:
        op.drop_constraint(
            "fk_groups_decision_maker_participant_id_participants",
            "groups",
            type_="foreignkey",
        )
        op.drop_column("groups", "decision_maker_participant_id")
