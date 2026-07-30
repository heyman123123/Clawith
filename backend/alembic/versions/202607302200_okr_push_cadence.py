"""Add OKR push cadence settings and same-day outreach ledger.

Revision ID: okr_push_cadence
Revises: group_workflows
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "okr_push_cadence"
down_revision: str | None = "group_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    columns = {column["name"] for column in inspector.get_columns("okr_settings")}
    if "push_cadence" not in columns:
        op.add_column(
            "okr_settings",
            sa.Column(
                "push_cadence",
                sa.String(length=20),
                nullable=False,
                server_default="both",
            ),
        )
    if "workflow_trigger_events" not in columns:
        op.add_column(
            "okr_settings",
            sa.Column(
                "workflow_trigger_events",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[\"stage_completed\",\"workflow_completed\"]'::jsonb"),
            ),
        )
    if "excluded_group_ids" not in columns:
        op.add_column(
            "okr_settings",
            sa.Column(
                "excluded_group_ids",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    if not inspector.has_table("okr_collection_outreach"):
        op.create_table(
            "okr_collection_outreach",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("member_type", sa.String(length=16), nullable=False),
            sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("report_date", sa.Date(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False, server_default="calendar"),
            sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.CheckConstraint(
                "member_type IN ('user', 'agent')",
                name="ck_okr_collection_outreach_member_type",
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "member_type",
                "member_id",
                "report_date",
                name="uq_okr_collection_outreach_day",
            ),
        )
        op.create_index(
            "ix_okr_collection_outreach_tenant_date",
            "okr_collection_outreach",
            ["tenant_id", "report_date"],
        )


def downgrade() -> None:
    inspector = _inspector()
    if inspector.has_table("okr_collection_outreach"):
        op.drop_index("ix_okr_collection_outreach_tenant_date", table_name="okr_collection_outreach")
        op.drop_table("okr_collection_outreach")
    columns = {column["name"] for column in inspector.get_columns("okr_settings")}
    for name in ("excluded_group_ids", "workflow_trigger_events", "push_cadence"):
        if name in columns:
            op.drop_column("okr_settings", name)
