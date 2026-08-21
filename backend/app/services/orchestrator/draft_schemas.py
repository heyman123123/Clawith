"""Draft / proposal Pydantic schemas for the Orchestrator.

These schemas describe the user's natural-language intent and the proposed
materialization (Group + Agents + OKR + Tasks). They are the wire format
between Orchestrator Service (LLM-driven) and the Draft Preview UI.
"""
from __future__ import annotations
from typing import Literal
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field


class AgentProposal(BaseModel):
    """A proposed agent role for the Group."""
    role: str
    name: str
    system_prompt: str = ""
    template_id: UUID | None = None
    is_new_template: bool = False
    suggested_visibility: Literal["user_private", "tenant_private", "public"] = "user_private"


class KeyResultProposal(BaseModel):
    title: str
    target_value: float = 100.0
    unit: str | None = None
    acceptance_artifact_paths: list[str] = Field(default_factory=list)


class OKRProposal(BaseModel):
    objective_title: str
    objective_description: str = ""
    key_results: list[KeyResultProposal] = Field(default_factory=list)


class TaskProposal(BaseModel):
    title: str
    description: str = ""
    assignee_role: str | None = None
    depends_on_titles: list[str] = Field(default_factory=list)
    estimated_artifacts: list[str] = Field(default_factory=list)


class GroupProposal(BaseModel):
    name: str
    description: str = ""


class DraftSummary(BaseModel):
    """LLM-parsed intent summary."""
    intent_category: str
    scope_summary: str
    key_constraints: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    """A user-visible proposal awaiting approval."""
    id: UUID
    tenant_id: UUID
    user_id: UUID
    user_message: str
    summary: DraftSummary
    group: GroupProposal
    members: list[AgentProposal]
    okr: OKRProposal | None = None
    tasks: list[TaskProposal] = Field(default_factory=list)
    template_visibility: Literal["user_private", "tenant_private", "public"] = "user_private"
    estimated_total_llm_tokens: int | None = None
    status: Literal["pending", "approved", "rejected", "expired", "error", "consumed"] = "pending"
    error_detail: dict | None = None
    created_at: datetime
    updated_at: datetime


class ProjectCreated(BaseModel):
    """AtomicCreator success result."""
    draft_id: UUID
    group_id: UUID
    chief_agent_id: UUID
    chief_run_id: UUID
    task_card_ids: list[UUID] = Field(default_factory=list)
    okr_objective_id: UUID | None = None
