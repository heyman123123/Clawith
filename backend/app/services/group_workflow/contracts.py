"""Strict, model-safe plans used to create a group workflow."""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field, ValidationError, model_validator


class GroupWorkflowPlanError(ValueError):
    pass


class WorkflowItemPlan(BaseModel):
    item_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=100)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    assignee_participant_id: uuid.UUID | None = None


class WorkflowStagePlan(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=80)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    requires_approval: bool = False
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    owner_participant_id: uuid.UUID | None = None
    items: list[WorkflowItemPlan] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _approval_has_gate(self) -> WorkflowStagePlan:
        if self.requires_approval and not self.acceptance_criteria:
            raise ValueError("approval stages require acceptance_criteria")
        return self


class WorkflowPlan(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(pattern=r"^(default|agile|product_research|ai)$")
    stages: list[WorkflowStagePlan] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _unique_keys_and_assignees(self) -> WorkflowPlan:
        stage_keys = [stage.key for stage in self.stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("stage keys must be unique")
        for stage in self.stages:
            item_keys = [item.item_key for item in stage.items]
            if len(item_keys) != len(set(item_keys)):
                raise ValueError(f"item keys must be unique in {stage.key}")
        return self


def validate_workflow_plan(value: object, *, participant_ids: set[uuid.UUID] | None = None) -> WorkflowPlan:
    try:
        plan = WorkflowPlan.model_validate(value)
    except ValidationError as exc:
        raise GroupWorkflowPlanError(str(exc)) from exc
    if participant_ids is not None:
        for stage in plan.stages:
            for participant_id in (stage.owner_participant_id, *(item.assignee_participant_id for item in stage.items)):
                if participant_id is not None and participant_id not in participant_ids:
                    raise GroupWorkflowPlanError("workflow references a participant outside the group")
    return plan


def clean_model_json(value: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE)
