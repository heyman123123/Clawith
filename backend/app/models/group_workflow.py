"""Durable, evidence-driven lifecycle state for native group collaboration."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GroupWorkflow(Base):
    __tablename__ = "group_workflows"
    __table_args__ = (
        CheckConstraint("source IN ('default', 'agile', 'product_research', 'ai')", name="ck_group_workflows_source"),
        CheckConstraint("status IN ('active', 'paused', 'awaiting_approval', 'completed')", name="ck_group_workflows_status"),
        UniqueConstraint("group_id", name="uq_group_workflows_group"),
        Index("ix_group_workflows_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    leader_participant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    current_stage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class GroupWorkflowStage(Base):
    __tablename__ = "group_workflow_stages"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'awaiting_approval', 'completed', 'blocked')", name="ck_group_workflow_stages_status"),
        UniqueConstraint("workflow_id", "position", name="uq_group_workflow_stages_position"),
        UniqueConstraint("workflow_id", "stage_key", name="uq_group_workflow_stages_key"),
        Index("ix_group_workflow_stages_workflow_status", "workflow_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_workflows.id", ondelete="CASCADE"), nullable=False)
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    requires_approval: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    acceptance_criteria: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    owner_participant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupWorkflowItem(Base):
    __tablename__ = "group_workflow_items"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'in_progress', 'blocked', 'awaiting_approval', 'done')", name="ck_group_workflow_items_status"),
        UniqueConstraint("stage_id", "item_key", name="uq_group_workflow_items_key"),
        Index("ix_group_workflow_items_stage_status", "stage_id", "status"),
        Index("ix_group_workflow_items_assignee_status", "assignee_participant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_workflows.id", ondelete="CASCADE"), nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_workflow_stages.id", ondelete="CASCADE"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_participant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class GroupWorkflowEvent(Base):
    __tablename__ = "group_workflow_events"
    __table_args__ = (
        CheckConstraint("dispatch_state IN ('none', 'pending', 'claimed', 'dispatched')", name="ck_group_workflow_events_dispatch"),
        UniqueConstraint("workflow_id", "idempotency_key", name="uq_group_workflow_events_idempotency"),
        Index("ix_group_workflow_events_workflow_created", "workflow_id", "created_at"),
        Index("ix_group_workflow_events_dispatch", "dispatch_state", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_workflows.id", ondelete="CASCADE"), nullable=False)
    stage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("group_workflow_stages.id", ondelete="SET NULL"))
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("group_workflow_items.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_participant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    dispatch_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'none'"))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GroupWorkflowDraft(Base):
    __tablename__ = "group_workflow_drafts"
    __table_args__ = (
        CheckConstraint("status IN ('generating', 'ready', 'failed', 'confirmed', 'cancelled')", name="ck_group_workflow_drafts_status"),
        Index("ix_group_workflow_drafts_group_created", "group_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    creator_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    plan: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'generating'"))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
