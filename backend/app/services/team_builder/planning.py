"""Strict team-design model contract, separate from in-group task planning."""

from __future__ import annotations

import json
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import User
from app.services.agent_runtime.model_capabilities import (
    PlatformModelConfigurationError,
    resolve_multi_agent_planning_model,
)
from app.services.ai_monitoring import ai_interaction_scope
from app.services.llm.client import LLMMessage
from app.services.llm.single_step import complete_llm_once
from app.services.team_builder.errors import TeamBuilderError


class TeamPlanMember(BaseModel):
    member_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=2, max_length=100)
    role_description: str = Field(min_length=1, max_length=500)
    responsibility: str = Field(min_length=1, max_length=2000)
    source: Literal["existing", "new"]
    existing_agent_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    skill_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    is_leader: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> TeamPlanMember:
        if self.source == "existing" and self.existing_agent_id is None:
            raise ValueError("existing members require existing_agent_id")
        if self.source == "new" and self.existing_agent_id is not None:
            raise ValueError("new members cannot include existing_agent_id")
        return self


class TeamPlanDelegation(BaseModel):
    from_member_key: str = Field(min_length=1, max_length=120)
    to_member_key: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=1000)


class TeamPlan(BaseModel):
    group_name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    phases: list[str] = Field(min_length=1, max_length=20)
    members: list[TeamPlanMember] = Field(min_length=1, max_length=20)
    delegations: list[TeamPlanDelegation] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_roster(self) -> TeamPlan:
        keys = [member.member_key for member in self.members]
        if len(set(keys)) != len(keys):
            raise ValueError("member_key values must be unique")
        if len([member for member in self.members if member.is_leader]) != 1:
            raise ValueError("exactly one member must be the group leader")
        member_keys = set(keys)
        for delegation in self.delegations:
            if delegation.from_member_key not in member_keys or delegation.to_member_key not in member_keys:
                raise ValueError("delegations must reference roster members")
        return self


_SYSTEM_PROMPT = """You design durable Clawith AI teams. Return exactly one JSON object, no Markdown.
Use exactly these fields: group_name, goal, assumptions, phases, members, delegations.
Each member has member_key, name, role_description, responsibility, source, existing_agent_id,
template_id, skill_ids, and is_leader. source is existing or new; exactly one member is_leader.
Each delegation has from_member_key, to_member_key, and instruction. The leader receives all human
directions and delegates work publicly to team members. Use existing only with an ID in candidate_agents.
Create new members when no candidate fits. Keep the team as small as possible."""


def validate_team_plan(payload: object) -> TeamPlan:
    try:
        return TeamPlan.model_validate(payload)
    except ValidationError as exc:
        raise TeamBuilderError("team_plan_invalid", str(exc), retryable=True) from exc


def _parse_json(content: str | None) -> object:
    if not content:
        raise TeamBuilderError("team_plan_empty", "Team planning model returned no content", retryable=True)
    value = content.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\\s*|\\s*```$", "", value, flags=re.IGNORECASE)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TeamBuilderError(
            "team_plan_invalid", "Team planning model returned invalid JSON", retryable=True
        ) from exc


def fallback_team_plan(requirement: str, *, group_name: str | None = None) -> TeamPlan:
    title = group_name.strip() if group_name and group_name.strip() else "新建协作团队"
    return TeamPlan(
        group_name=title,
        goal=requirement.strip(),
        assumptions=["团队将根据群主的拆解公开协作，并在群内同步结果。"],
        phases=["群主澄清目标并拆解工作", "成员执行并公开反馈", "群主汇总结果与下一步"],
        members=[
            TeamPlanMember(
                member_key="team_leader",
                name="团队群主",
                role_description="团队负责人和任务编排者",
                responsibility="接收用户目标，拆解任务，公开分发给成员并汇总进展。",
                source="new",
                is_leader=True,
            ),
            TeamPlanMember(
                member_key="delivery_specialist",
                name="交付专员",
                role_description="执行与交付支持",
                responsibility="完成群主分配的具体工作，并在群内提交可复核结果。",
                source="new",
            ),
        ],
        delegations=[
            TeamPlanDelegation(
                from_member_key="team_leader",
                to_member_key="delivery_specialist",
                instruction="根据已确认目标完成具体交付，并公开报告结果与阻塞项。",
            )
        ],
    )


async def generate_team_plan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    requirement: str,
    constraints: dict,
    group_name: str | None = None,
) -> TeamPlan:
    candidates_result = await db.execute(
        select(Agent)
        .where(
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
            Agent.status.in_(("creating", "running", "idle")),
            Agent.is_expired.is_(False),
        )
        .order_by(Agent.created_at.desc())
        .limit(100)
    )
    candidates = [
        {"id": str(agent.id), "name": agent.name, "role_description": agent.role_description}
        for agent in candidates_result.scalars().all()
    ]
    try:
        model = await resolve_multi_agent_planning_model(db, tenant_id=tenant_id)
    except PlatformModelConfigurationError:
        return fallback_team_plan(requirement, group_name=group_name)
    request = {
        "requirement": requirement,
        "requested_group_name": group_name,
        "constraints": constraints,
        "candidate_agents": candidates,
        "requesting_user": user.display_name,
    }
    try:
        with ai_interaction_scope(tenant_id=tenant_id, source="team_planning"):
            completion = await complete_llm_once(
                model,
                [
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=json.dumps(request, ensure_ascii=False)),
                ],
                tools=None,
                agent_id=None,
                supports_vision=False,
            )
    except Exception as exc:
        raise TeamBuilderError("team_plan_model_failed", "Team planning model call failed", retryable=True) from exc
    if completion.tool_calls:
        raise TeamBuilderError("team_plan_invalid", "Team planning model attempted to call a tool", retryable=True)
    return validate_team_plan(_parse_json(completion.content))
