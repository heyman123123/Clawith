"""Add delivery approval + human review queue

需求 §3.4 + §4.11 + §8.3 — the delivery dual-dimension scoring rubric
(60% quality / 40% coverage, ≥90 pass) and the cross-cutting human
review queue (审批卡 / 决策卡 / 高危技能审核 / 质检异常人工复核).

Idempotent: ``001_initial_schema`` runs ``Base.metadata.create_all(checkfirst=True)``
and will already create these tables whenever the ORM models are imported.
A bare ``op.create_table`` then fails with DuplicateTableError on every
entrypoint retry. Mirror the skill-market / AO migrations and skip when
the relation already exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "add_delivery_review"
down_revision = "add_skill_market_and_metrics"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()

    if "workflow_delivery_approvals" not in tables:
        op.create_table(
            "workflow_delivery_approvals",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "workflow_id",
                UUID(as_uuid=True),
                sa.ForeignKey("project_workflows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("round_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("quality_score", sa.Float(), nullable=True),
            sa.Column("coverage_score", sa.Float(), nullable=True),
            sa.Column("final_score", sa.Float(), nullable=True),
            sa.Column(
                "decision",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "delivery_manager_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "quality_agent_id",
                UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("coverage_notes", sa.Text(), nullable=True),
            sa.Column("quality_notes", sa.Text(), nullable=True),
            sa.Column(
                "rectification_items",
                JSONB,
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "decision IN ('pending','approved','rejected','withdrawn')",
                name="ck_workflow_delivery_approvals_decision",
            ),
            sa.CheckConstraint(
                "round_no >= 1 AND round_no <= 3",
                name="ck_workflow_delivery_approvals_round",
            ),
        )

    delivery_indexes = _indexes("workflow_delivery_approvals")
    if "ix_workflow_delivery_approvals_workflow" not in delivery_indexes:
        op.create_index(
            "ix_workflow_delivery_approvals_workflow",
            "workflow_delivery_approvals",
            ["workflow_id", "round_no"],
        )
    if "ix_workflow_delivery_approvals_tenant_id" not in delivery_indexes:
        op.create_index(
            "ix_workflow_delivery_approvals_tenant_id",
            "workflow_delivery_approvals",
            ["tenant_id"],
        )

    tables = _tables()
    if "workflow_human_reviews" not in tables:
        op.create_table(
            "workflow_human_reviews",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "workflow_id",
                UUID(as_uuid=True),
                sa.ForeignKey("project_workflows.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "skill_id",
                UUID(as_uuid=True),
                sa.ForeignKey("skills.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "agent_id",
                UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'open'"),
            ),
            sa.Column(
                "payload",
                JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("decision_notes", sa.Text(), nullable=True),
            sa.Column(
                "requester_user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "reviewer_user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "kind IN ('high_risk_skill','qc_anomaly_rectification',"
                "'shareholder_decision','approval_card','decision_card','rectification')",
                name="ck_workflow_human_reviews_kind",
            ),
            sa.CheckConstraint(
                "status IN ('open','approved','rejected','withdrawn','auto_resolved')",
                name="ck_workflow_human_reviews_status",
            ),
        )

    review_indexes = _indexes("workflow_human_reviews")
    if "ix_workflow_human_reviews_kind_status" not in review_indexes:
        op.create_index(
            "ix_workflow_human_reviews_kind_status",
            "workflow_human_reviews",
            ["kind", "status"],
        )
    if "ix_workflow_human_reviews_workflow" not in review_indexes:
        op.create_index(
            "ix_workflow_human_reviews_workflow",
            "workflow_human_reviews",
            ["workflow_id"],
        )


def downgrade() -> None:
    tables = _tables()
    if "workflow_human_reviews" in tables:
        indexes = _indexes("workflow_human_reviews")
        if "ix_workflow_human_reviews_workflow" in indexes:
            op.drop_index(
                "ix_workflow_human_reviews_workflow",
                table_name="workflow_human_reviews",
            )
        if "ix_workflow_human_reviews_kind_status" in indexes:
            op.drop_index(
                "ix_workflow_human_reviews_kind_status",
                table_name="workflow_human_reviews",
            )
        op.drop_table("workflow_human_reviews")
    if "workflow_delivery_approvals" in tables:
        indexes = _indexes("workflow_delivery_approvals")
        if "ix_workflow_delivery_approvals_tenant_id" in indexes:
            op.drop_index(
                "ix_workflow_delivery_approvals_tenant_id",
                table_name="workflow_delivery_approvals",
            )
        if "ix_workflow_delivery_approvals_workflow" in indexes:
            op.drop_index(
                "ix_workflow_delivery_approvals_workflow",
                table_name="workflow_delivery_approvals",
            )
        op.drop_table("workflow_delivery_approvals")
