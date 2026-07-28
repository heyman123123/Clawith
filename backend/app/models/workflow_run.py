"""AO workflow run + step records.

One ``ProjectWorkflow`` represents a user-approved team plan. Once HR picks a
proposal, ``provision_team_from_plan`` creates the execution group, composes
the AO YAML, and seeds one ``WorkflowRunStep`` row per DAG node so P1.4 can
resume from any step without re-parsing the YAML. The schema mirrors the
``202607271300_add_ao_workflow_runs`` Alembic migration (columns use the same
names) so ``Base.metadata.create_all`` for tests and production stay in sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def text_value(sql: str):
    """Render a raw SQL fragment as a server_default literal."""
    return text(sql)


class WorkflowRun(Base):
    """Logical AO workflow execution attached to a ProjectWorkflow.

    Note: a dedicated ``workflow_runs`` table is reserved for a future P2
    milestone that separates multi-version runs from the workflow itself.
    For P1.3 the runtime state lives on ``ProjectWorkflow`` columns; this
    class is declared here to keep import-side compatibility for callers
    that already reference the symbol.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','composing','queued','running','paused',"
            "'awaiting_approval','quality_retry','succeeded','failed','cancelled')",
            name="ck_workflow_runs_status",
        ),
        PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        Index("ix_workflow_runs_tenant_workflow", "tenant_id", "workflow_id"),
        Index("ix_workflow_runs_group", "group_id"),
        Index("ix_workflow_runs_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_workflow_runs_tenant_id_tenants", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_workflows.id", name="fk_workflow_runs_workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_workflow_runs_group_id_groups", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text_value("'{}'::jsonb")
    )
    scheduler_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", name="fk_workflow_runs_scheduler_agent_id_agents"), nullable=True
    )
    quality_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", name="fk_workflow_runs_quality_agent_id_agents"), nullable=True
    )
    delivery_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", name="fk_workflow_runs_delivery_agent_id_agents"), nullable=True
    )
    delivery_manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", name="fk_workflow_runs_delivery_manager_id_users"), nullable=True
    )
    executor_agent_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    stakeholder_agent_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default=text_value("'draft'"))
    asset_dir_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text_value("1"))
    final_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    total_tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text_value("0"))
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text_value("'{}'::jsonb")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WorkflowRunStep(Base):
    """One step in a workflow run, mirroring an AO DAG node.

    Schema matches ``202607271300_add_ao_workflow_runs`` migration. The
    combination ``(workflow_id, step_key)`` is unique so P1.4 can upsert by
    that pair without races.
    """

    __tablename__ = "workflow_run_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','awaiting_approval','quality_checking','succeeded',"
            "'quality_failed','failed','skipped','cancelled')",
            name="ck_workflow_run_steps_status",
        ),
        PrimaryKeyConstraint("id", name="pk_workflow_run_steps"),
        UniqueConstraint(
            "workflow_id",
            "step_key",
            name="uq_workflow_run_steps_workflow_key",
        ),
        Index("ix_workflow_run_steps_tenant_id", "tenant_id"),
        Index(
            "ix_workflow_run_steps_workflow_status",
            "workflow_id",
            "status",
        ),
        Index(
            "ix_workflow_run_steps_step_order",
            "workflow_id",
            "step_order",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_workflow_run_steps_tenant_id_tenants", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "project_workflows.id",
            name="fk_workflow_run_steps_workflow_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    role_path: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_workflow_run_steps_agent_id_agents", ondelete="SET NULL"),
        nullable=True,
    )
    task_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_refs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_var: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depends_on: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text_value("'[]'::jsonb")
    )
    condition_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text_value("'pending'"),
    )
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text_value("0")
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default=text_value("2")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WorkflowStepAsset(Base):
    """One file AO/Clawith wrote under ``ao-output/<workflow_id>/<stage>/``.

    P2.3 mirrors the file system into the database so the group workspace
    can render an asset list without re-scanning ``ao-output``.  The
    ``category`` mirrors the four-stage directory layout produced by
    ``scheduler_tools.init_workflow_dir``; ``rel_path`` is the path under
    the workflow output root and ``abs_path`` is the resolved on-disk
    location.  ``content_hash`` (sha256) and ``byte_size`` make duplicate
    writes idempotent and let ``sync_workflow_assets`` flag missing rows.
    """

    __tablename__ = "workflow_step_assets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_step_assets"),
        Index(
            "ix_workflow_step_assets_workflow_cat",
            "workflow_id",
            "category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_workflow_step_assets_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "project_workflows.id",
            name="fk_workflow_step_assets_workflow_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "workflow_run_steps.id",
            name="fk_workflow_step_assets_step_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    rel_path: Mapped[str] = mapped_column(String(512), nullable=False)
    abs_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )