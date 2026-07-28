"""Add AO workflow run/step storage.

Revision ID: add_ao_workflow_runs
Revises: add_board_escalations
Create Date: 2026-07-27 13:00:00

Adds the persistence side of the AO integration:

* ``project_workflows`` gets runtime columns the scheduler needs (yaml text,
  output directory, AO provider/model config, status enum, totals).
* New ``workflow_run_steps`` table holds one row per AO step so the scheduler
  can map AO progress to Clawith chat messages and quality records without
  re-parsing the YAML every time.
* New ``workflow_step_assets`` table tracks files AO writes so the group
  workspace can surface them without scanning ``ao-output`` every time.

This migration is intentionally additive: it never drops existing columns or
tables. Down migration reverses the additions in opposite order.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_ao_workflow_runs"
down_revision: str | None = "add_board_escalations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _foreign_keys(table: str) -> set[str]:
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if foreign_key.get("name")
    }


def upgrade() -> None:
    workflow_cols = _columns("project_workflows")

    additions = [
        ("requirement_id", postgresql.UUID(as_uuid=True), True),
        ("yaml_content", sa.Text(), True),
        ("template_key_ao", sa.String(length=64), True),
        ("ao_run_dir", sa.String(length=512), True),
        ("scheduler_agent_id", postgresql.UUID(as_uuid=True), True),
        ("quality_agent_id", postgresql.UUID(as_uuid=True), True),
        ("delivery_agent_id", postgresql.UUID(as_uuid=True), True),
        ("delivery_manager_id", postgresql.UUID(as_uuid=True), True),
        ("executor_agent_ids", postgresql.JSONB(astext_type=sa.Text()), False),
        ("stakeholder_agent_ids", postgresql.JSONB(astext_type=sa.Text()), False),
        ("member_count", sa.Integer(), False),
        ("ao_provider", sa.String(length=32), True),
        ("ao_model", sa.String(length=64), True),
        ("ao_concurrency", sa.Integer(), True),
        ("ao_max_retries", sa.Integer(), True),
        ("quality_threshold", sa.Integer(), True),
        ("skill_permission_level", sa.String(length=16), True),
        ("allow_custom_skill", sa.Boolean(), False),
        ("total_input_tokens", sa.Integer(), True),
        ("total_output_tokens", sa.Integer(), True),
        ("final_score", sa.Integer(), True),
        ("started_at", sa.DateTime(timezone=True), True),
        ("completed_at", sa.DateTime(timezone=True), True),
        ("last_resume_at", sa.DateTime(timezone=True), True),
        ("last_event_at", sa.DateTime(timezone=True), True),
    ]
    for col_name, col_type, nullable in additions:
        if col_name in workflow_cols:
            continue
        if nullable:
            op.add_column("project_workflows", sa.Column(col_name, col_type, nullable=True))
        else:
            server_default = (
                "0" if col_name in {"member_count", "total_input_tokens", "total_output_tokens"} else None
            )
            op.add_column(
                "project_workflows",
                sa.Column(
                    col_name,
                    col_type,
                    nullable=False,
                    server_default=server_default,
                ),
            )
    if "allow_custom_skill" in _columns("project_workflows"):
        op.execute(
            "UPDATE project_workflows SET allow_custom_skill = false WHERE allow_custom_skill IS NULL"
        )

    workflow_indexes = _indexes("project_workflows")
    if "ix_project_workflows_requirement_id" not in workflow_indexes:
        op.create_index(
            "ix_project_workflows_requirement_id",
            "project_workflows",
            ["requirement_id"],
        )
    if "ix_project_workflows_template_key_ao" not in workflow_indexes:
        op.create_index(
            "ix_project_workflows_template_key_ao",
            "project_workflows",
            ["template_key_ao"],
        )
    if "ix_project_workflows_started_at" not in workflow_indexes:
        op.create_index(
            "ix_project_workflows_started_at",
            "project_workflows",
            ["started_at"],
        )

    tables = sa.inspect(op.get_bind()).get_table_names()
    if "workflow_run_steps" not in tables:
        op.create_table(
            "workflow_run_steps",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("step_key", sa.String(length=64), nullable=False),
            sa.Column("step_order", sa.Integer(), nullable=False),
            sa.Column("role_path", sa.String(length=200), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("task_summary", sa.Text(), nullable=True),
            sa.Column("input_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("output_var", sa.String(length=64), nullable=True),
            sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("condition_expr", sa.Text(), nullable=True),
            sa.Column("acceptance_text", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("quality_score", sa.Integer(), nullable=True),
            sa.Column("quality_feedback", sa.Text(), nullable=True),
            sa.Column("output_excerpt", sa.Text(), nullable=True),
            sa.Column("output_file", sa.String(length=512), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["project_workflows.id"],
                name="fk_workflow_run_steps_workflow_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                name="fk_workflow_run_steps_agent_id_agents",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_workflow_run_steps_tenant_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_workflow_run_steps"),
            sa.UniqueConstraint(
                "workflow_id",
                "step_key",
                name="uq_workflow_run_steps_workflow_key",
            ),
        )

    step_indexes = _indexes("workflow_run_steps")
    if "ix_workflow_run_steps_tenant_id" not in step_indexes:
        op.create_index("ix_workflow_run_steps_tenant_id", "workflow_run_steps", ["tenant_id"])
    if "ix_workflow_run_steps_workflow_status" not in step_indexes:
        op.create_index(
            "ix_workflow_run_steps_workflow_status",
            "workflow_run_steps",
            ["workflow_id", "status"],
        )
    if "ix_workflow_run_steps_step_order" not in step_indexes:
        op.create_index(
            "ix_workflow_run_steps_step_order",
            "workflow_run_steps",
            ["workflow_id", "step_order"],
        )

    if "workflow_step_assets" not in tables:
        op.create_table(
            "workflow_step_assets",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("rel_path", sa.String(length=512), nullable=False),
            sa.Column("abs_path", sa.String(length=1024), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["project_workflows.id"],
                name="fk_workflow_step_assets_workflow_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["step_id"],
                ["workflow_run_steps.id"],
                name="fk_workflow_step_assets_step_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_workflow_step_assets_tenant_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_workflow_step_assets"),
        )

    asset_indexes = _indexes("workflow_step_assets")
    if "ix_workflow_step_assets_workflow_cat" not in asset_indexes:
        op.create_index(
            "ix_workflow_step_assets_workflow_cat",
            "workflow_step_assets",
            ["workflow_id", "category"],
        )


def downgrade() -> None:
    asset_indexes = _indexes("workflow_step_assets")
    if "ix_workflow_step_assets_workflow_cat" in asset_indexes:
        op.drop_index("ix_workflow_step_assets_workflow_cat", table_name="workflow_step_assets")
    if "workflow_step_assets" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("workflow_step_assets")

    step_indexes = _indexes("workflow_run_steps")
    for idx in (
        "ix_workflow_run_steps_step_order",
        "ix_workflow_run_steps_workflow_status",
        "ix_workflow_run_steps_tenant_id",
    ):
        if idx in step_indexes:
            op.drop_index(idx, table_name="workflow_run_steps")
    if "workflow_run_steps" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("workflow_run_steps")

    workflow_indexes = _indexes("project_workflows")
    for idx in (
        "ix_project_workflows_started_at",
        "ix_project_workflows_template_key_ao",
        "ix_project_workflows_requirement_id",
    ):
        if idx in workflow_indexes:
            op.drop_index(idx, table_name="project_workflows")

    drop_cols = [
        "last_event_at",
        "last_resume_at",
        "completed_at",
        "started_at",
        "final_score",
        "total_output_tokens",
        "total_input_tokens",
        "allow_custom_skill",
        "skill_permission_level",
        "quality_threshold",
        "ao_max_retries",
        "ao_concurrency",
        "ao_model",
        "ao_provider",
        "member_count",
        "stakeholder_agent_ids",
        "executor_agent_ids",
        "delivery_manager_id",
        "delivery_agent_id",
        "quality_agent_id",
        "scheduler_agent_id",
        "ao_run_dir",
        "template_key_ao",
        "yaml_content",
        "requirement_id",
    ]
    workflow_cols = _columns("project_workflows")
    for col in drop_cols:
        if col in workflow_cols:
            op.drop_column("project_workflows", col)