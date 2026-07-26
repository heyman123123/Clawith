"""Project workflow models for HR-built teams and leader-led project groups."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, PrimaryKeyConstraint, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectWorkflow(Base):
    """A user-approved team plan and the project group created from it."""

    __tablename__ = "project_workflows"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_project_workflows"),
        Index("ix_project_workflows_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_project_workflows_tenant_id_tenants", ondelete="RESTRICT"),
        nullable=False,
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", name="fk_project_workflows_creator_id_users"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    requirements: Mapped[str] = mapped_column(Text, nullable=False)
    # planning | provisioning | active | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planning")
    team_plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", name="fk_project_workflows_group_id_groups"), nullable=True
    )
    # The governance surface is intentionally separate from the execution
    # group. Project members report here, deliberate with the user, then the
    # confirmed instruction is routed back to the project group leader.
    decision_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", name="fk_project_workflows_decision_group_id_groups"),
        nullable=True,
    )
    group_leader_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", name="fk_project_workflows_group_leader_agent_id_agents"), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProjectWorkflowMember(Base):
    """An Agent provisioned for a project workflow."""

    __tablename__ = "project_workflow_members"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_project_workflow_members"),
        Index("ix_project_workflow_members_workflow_id", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_workflows.id", name="fk_project_workflow_members_workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", name="fk_project_workflow_members_agent_id_agents"), nullable=False
    )
    role_key: Mapped[str] = mapped_column(String(64), nullable=False)
    role_title: Mapped[str] = mapped_column(String(100), nullable=False)
    is_group_leader: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProjectMilestone(Base):
    """A dependency-driven phase marker for a project workflow."""

    __tablename__ = "project_milestones"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_project_milestones"),
        Index("ix_project_milestones_workflow_order", "workflow_id", "order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_workflows.id", name="fk_project_milestones_workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending | active | done | cancelled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", name="fk_project_milestones_created_by_agent_id_agents", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectDecision(Base):
    """A user-owned decision requested by a project task or its group leader."""

    __tablename__ = "project_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_project_decisions"),
        Index("ix_project_decisions_group_status", "group_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_workflows.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    review_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    requesting_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    # pending | answered | cancelled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ShareholderGroup(Base):
    """One tenant-level governance group for cross-project shareholder decisions."""

    __tablename__ = "shareholder_groups"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_shareholder_groups"),
        Index("uq_shareholder_groups_tenant_id", "tenant_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ShareholderDispatch(Base):
    """Auditable company-level decision sent to a project's governance group."""

    __tablename__ = "shareholder_dispatches"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_shareholder_dispatches"),
        Index("ix_shareholder_dispatches_group_created", "shareholder_group_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shareholder_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shareholder_groups.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_workflows.id", ondelete="CASCADE"), nullable=False
    )
    target_decision_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="dispatched")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
