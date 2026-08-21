"""Checkpoint state schemas for the Chief Runtime.

The Chief's runtime state lives in the LangGraph Checkpoint (single source
of truth per C1). These Pydantic models describe the shape of that state
so the Checkpoint serializer and consumers can reason about it.
"""
from __future__ import annotations
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class CardMoveAction(BaseModel):
    card_id: UUID
    to_column: str
    expected_version: int


class CardAssignAction(BaseModel):
    card_id: UUID
    assignee_agent_id: UUID
    expected_version: int


class DispatchTaskAction(BaseModel):
    agent_role: str
    title: str
    description: str = ""


class OKRProgressAction(BaseModel):
    key_result_id: UUID
    progress_delta: float = 0.0
    note: str = ""


class ActionPlan(BaseModel):
    """Reasoner's output: a batch of side-effectful actions to apply."""

    review_artifact_paths: list[str] = Field(default_factory=list)
    move_cards: list[CardMoveAction] = Field(default_factory=list)
    assign_cards: list[CardAssignAction] = Field(default_factory=list)
    dispatch_tasks: list[DispatchTaskAction] = Field(default_factory=list)
    update_okr_progress: list[OKRProgressAction] = Field(default_factory=list)
    notify_user: str | None = None


class GroupState(BaseModel):
    """Snapshot of group state the Reasoner considers when deciding."""

    group_id: UUID
    current_okr_stage: str = "pending"
    pending_cards: list[dict] = Field(default_factory=list)
    in_flight_cards: list[dict] = Field(default_factory=list)
    verified_artifacts: list[str] = Field(default_factory=list)
    failure_count: int = 0


# Chief Runtime status values for chief_runs.status column
CHIEF_STATUS_ACTIVE = "active"
CHIEF_STATUS_PAUSED = "paused"
CHIEF_STATUS_DEGRADED = "degraded"
CHIEF_STATUS_STOPPED = "stopped"

CHIEF_RUN_KIND = "orchestrator"  # run_kind column value (C1 compliance)
