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


class TeamPlanWorkflowStage(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    requires_approval: bool = False


class TeamPlanWorkflow(BaseModel):
    preset: Literal["default", "agile", "product_research", "custom"] = "default"
    name: str = Field(min_length=1, max_length=200)
    stages: list[TeamPlanWorkflowStage] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def validate_stages(self) -> TeamPlanWorkflow:
        keys = [stage.key for stage in self.stages]
        if len(set(keys)) != len(keys):
            raise ValueError("workflow stage keys must be unique")
        for stage in self.stages:
            if stage.requires_approval and not stage.goal.strip():
                raise ValueError(f"approval stage {stage.key} requires a goal")
        return self


class TeamPlan(BaseModel):
    group_name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    phases: list[str] = Field(min_length=1, max_length=20)
    members: list[TeamPlanMember] = Field(min_length=1, max_length=20)
    delegations: list[TeamPlanDelegation] = Field(default_factory=list, max_length=100)
    workflow: TeamPlanWorkflow | None = None
    sop: str | None = Field(default=None, max_length=12_000)

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
        if self.workflow is None:
            object.__setattr__(self, "workflow", workflow_from_preset("default", goal=self.goal))
        if not (self.sop or "").strip():
            object.__setattr__(self, "sop", build_team_sop(self))
        return self


def build_team_sop(plan: TeamPlan) -> str:
    """Compose the group announcement SOP every Agent must follow."""
    leader = next((member for member in plan.members if member.is_leader), None)
    leader_name = leader.name if leader else "群主"
    member_lines = [
        f"- **{member.name}**（{'群主' if member.is_leader else '成员'}）：{member.role_description} — {member.responsibility}"
        for member in plan.members
    ]
    workflow = plan.workflow
    stage_lines: list[str] = []
    if workflow is not None:
        for index, stage in enumerate(workflow.stages, start=1):
            gate = "；需决策者确认后进入下一阶段" if stage.requires_approval else ""
            stage_lines.append(f"{index}. **{stage.title}**：{stage.goal}{gate}")
    return "\n".join(
        [
            f"# {plan.group_name} · 协作 SOP",
            "",
            "## 团队目标",
            plan.goal.strip() or "推进群协作目标",
            "",
            "## 角色与职责",
            *member_lines,
            "",
            "## 工作流阶段",
            *(stage_lines or ["1. 按群主编排推进当前目标"]),
            "",
            "## 全体 Agent 必须遵循",
            f"1. **群主编排**：{leader_name} 接收人类目标，在群内公开拆解并分派任务；成员不得绕过群主私自改目标。",
            "2. **公开协作**：重要进展、阻塞、交付必须在群内公开同步，禁止只在私聊闭环。",
            "3. **@ 协议**：需要唤醒其他 Agent 时，必须先调用 `at` 工具传入其 participant_id，再在正文写可见 `@名字`；禁止只写 @ 名字。",
            "4. **证据推进**：完成工作后用工作流工具提交证据（submit_evidence 等），不要口头声称完成却不交证据。",
            "5. **项目级拍板**：阶段审批与项目决策找群内**决策者**，不要把项目拍板推给人类管理员或普通成员。",
            "6. **例外升级**：涉及对外沟通、外部部署、财务或不确定事项，由决策者私聊人类管理员确认后再执行。",
            "7. **主动推进**：收到指令立即行动，禁止干等心跳或定时；阻塞要立刻公开说明并 @ 相关角色。",
            "8. **本公告优先**：本 SOP 注入每位被 @ Agent 的上下文，所有成员必须优先遵守。",
            "",
            "## 阶段推进约定",
            "- 成员交付 → 群主核对与催证据 → 决策者对审批门拍板 → 决策结论公开 @ 群主继续编排。",
            "- 日常执行问题找群主；项目级是否通过找决策者。",
        ]
    )


def workflow_from_preset(
    preset: Literal["default", "agile", "product_research"] | str,
    *,
    goal: str,
) -> TeamPlanWorkflow:
    from app.services.group_workflow.templates import preset_workflow

    kind = preset if preset in {"default", "agile", "product_research"} else "default"
    plan = preset_workflow(kind, goal=goal.strip() or "推进群协作目标")
    return TeamPlanWorkflow(
        preset=kind,  # type: ignore[arg-type]
        name=plan.name,
        stages=[
            TeamPlanWorkflowStage(
                key=stage.key,
                title=stage.title,
                goal=stage.goal,
                requires_approval=stage.requires_approval,
            )
            for stage in plan.stages
        ],
    )


def team_workflow_to_workflow_plan(
    workflow: TeamPlanWorkflow,
    *,
    goal: str,
    leader_participant_id: uuid.UUID | None,
):
    from app.services.group_workflow.contracts import WorkflowItemPlan, WorkflowPlan, WorkflowStagePlan

    source = "ai" if workflow.preset == "custom" else workflow.preset
    if source not in {"default", "agile", "product_research", "ai"}:
        source = "ai"
    stages: list[WorkflowStagePlan] = []
    for stage in workflow.stages:
        criteria = ["决策者确认交付满足当前阶段目标"] if stage.requires_approval else []
        stages.append(
            WorkflowStagePlan(
                key=stage.key,
                title=stage.title,
                goal=stage.goal or goal,
                requires_approval=stage.requires_approval,
                acceptance_criteria=criteria,
                owner_participant_id=leader_participant_id,
                items=[
                    WorkflowItemPlan(
                        item_key=f"{stage.key}_deliverable",
                        title=stage.title,
                        description=stage.goal or goal,
                        assignee_participant_id=leader_participant_id,
                    )
                ],
            )
        )
    return WorkflowPlan(name=workflow.name, source=source, stages=stages)


_SYSTEM_PROMPT = """You design durable Clawith AI teams. Return exactly one JSON object, no Markdown.
Use exactly these fields: group_name, goal, assumptions, phases, members, delegations.
phases must be an array of plain strings (phase titles), not objects.
assumptions must be an array of plain strings.
Each member has member_key, name, role_description, responsibility, source, existing_agent_id,
template_id, skill_ids, and is_leader. source is existing or new; exactly one member is_leader.
template_id and skill_ids must be real UUIDs from the platform, or null / []. Never invent slug names.
Each delegation has from_member_key, to_member_key, and instruction. The leader receives all human
directions and delegates work publicly to team members. Use existing only with an ID in candidate_agents.
Create new members when no candidate fits. Keep the team as small as possible.
Do not invent workflow stages here; the platform attaches a workflow template separately."""


_REVISE_SYSTEM_PROMPT = """You revise an existing Clawith team plan. Return exactly one JSON object, no Markdown.
Keep the same schema: group_name, goal, assumptions, phases, members, delegations, workflow, and sop.
workflow has preset (default|agile|product_research|custom), name, and stages[{key,title,goal,requires_approval}].
sop is the group announcement markdown that every Agent must follow (roles, stage flow, collaboration rules).
When changing workflow stages set preset to custom. Keep exactly one is_leader. Stage keys must be unique slug ids.
Honor the revise_scope:
- members = change roster/delegations; keep workflow unless feedback forces a tiny rename; refresh sop to match roles
- workflow = change workflow stages/gates; keep members; refresh sop stage section
- both = adjust roles and/or workflow together based on feedback
Always treat current_plan as the source of truth. Apply the user's feedback as surgical edits, not a full rewrite
unless they ask to rebuild. After edits, regenerate sop so it matches the final roles and workflow."""


def _revision_focus(plan: TeamPlan) -> dict:
    """Compact view of the current plan so the model revises roles/workflow intentionally."""
    workflow = plan.workflow
    return {
        "group_name": plan.group_name,
        "goal": plan.goal,
        "leaders": [
            {"member_key": member.member_key, "name": member.name, "responsibility": member.responsibility}
            for member in plan.members
            if member.is_leader
        ],
        "members": [
            {
                "member_key": member.member_key,
                "name": member.name,
                "role_description": member.role_description,
                "responsibility": member.responsibility,
                "source": member.source,
                "is_leader": member.is_leader,
            }
            for member in plan.members
        ],
        "workflow_name": workflow.name if workflow else None,
        "workflow_preset": workflow.preset if workflow else None,
        "stages": [
            {
                "key": stage.key,
                "title": stage.title,
                "goal": stage.goal,
                "requires_approval": stage.requires_approval,
            }
            for stage in (workflow.stages if workflow else [])
        ],
        "sop_preview": (plan.sop or "")[:1500],
    }


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("name", "title", "phase", "summary", "description", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None
    return None


def _as_uuid(value: object) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _normalize_team_plan_payload(payload: object) -> object:
    """Coerce common LLM shape drift before strict Pydantic validation."""
    if not isinstance(payload, dict):
        return payload
    data = dict(payload)

    phases = data.get("phases")
    if isinstance(phases, list):
        normalized_phases: list[str] = []
        for item in phases:
            text = _as_text(item)
            if text:
                normalized_phases.append(text[:500])
        data["phases"] = normalized_phases

    assumptions = data.get("assumptions")
    if isinstance(assumptions, list):
        data["assumptions"] = [
            text for text in (_as_text(item) for item in assumptions) if text
        ][:20]

    members = data.get("members")
    if isinstance(members, list):
        normalized_members: list[dict] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            row = dict(member)
            row["template_id"] = _as_uuid(row.get("template_id"))
            skill_ids = row.get("skill_ids") or []
            if isinstance(skill_ids, list):
                row["skill_ids"] = [
                    skill_id
                    for skill_id in (_as_uuid(item) for item in skill_ids)
                    if skill_id is not None
                ]
            else:
                row["skill_ids"] = []
            existing_raw = row.get("existing_agent_id")
            existing_id = _as_uuid(existing_raw)
            row["existing_agent_id"] = existing_id
            # LLM often invents slug agent ids; demote those to new hires.
            if (
                row.get("source") == "existing"
                and existing_id is None
                and existing_raw not in (None, "")
            ):
                row["source"] = "new"
            normalized_members.append(row)
        data["members"] = normalized_members

    workflow = data.get("workflow")
    if not isinstance(workflow, dict) or not workflow.get("stages"):
        goal_text = _as_text(data.get("goal")) or "推进群协作目标"
        data["workflow"] = workflow_from_preset("default", goal=goal_text).model_dump(mode="json")
    else:
        stages = workflow.get("stages")
        if isinstance(stages, list):
            normalized_stages: list[dict] = []
            for index, stage in enumerate(stages):
                if not isinstance(stage, dict):
                    continue
                row = dict(stage)
                key = _as_text(row.get("key")) or f"stage_{index + 1}"
                row["key"] = re.sub(r"[^a-z0-9_-]+", "-", key.lower()).strip("-") or f"stage_{index + 1}"
                row["title"] = _as_text(row.get("title")) or row["key"]
                row["goal"] = _as_text(row.get("goal")) or (_as_text(data.get("goal")) or "推进")
                row["requires_approval"] = bool(row.get("requires_approval"))
                normalized_stages.append(row)
            workflow = dict(workflow)
            workflow["stages"] = normalized_stages
            if not _as_text(workflow.get("name")):
                workflow["name"] = "协作推进"
            preset = str(workflow.get("preset") or "custom")
            if preset not in {"default", "agile", "product_research", "custom"}:
                workflow["preset"] = "custom"
            data["workflow"] = workflow

    return data


def validate_team_plan(payload: object) -> TeamPlan:
    try:
        return TeamPlan.model_validate(_normalize_team_plan_payload(payload))
    except ValidationError as exc:
        raise TeamBuilderError("team_plan_invalid", str(exc), retryable=True) from exc


def _parse_json(content: str | None) -> object:
    if not content:
        raise TeamBuilderError("team_plan_empty", "Team planning model returned no content", retryable=True)
    value = content.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TeamBuilderError(
            "team_plan_invalid", "Team planning model returned invalid JSON", retryable=True
        ) from exc


def fallback_team_plan(
    requirement: str,
    *,
    group_name: str | None = None,
    workflow_preset: str = "default",
) -> TeamPlan:
    title = group_name.strip() if group_name and group_name.strip() else "新建协作团队"
    goal = requirement.strip()
    return TeamPlan(
        group_name=title,
        goal=goal,
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
        workflow=workflow_from_preset(workflow_preset, goal=goal),
    )


async def generate_team_plan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    requirement: str,
    constraints: dict,
    group_name: str | None = None,
    workflow_preset: str = "default",
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
        return fallback_team_plan(requirement, group_name=group_name, workflow_preset=workflow_preset)
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
    plan = validate_team_plan(_parse_json(completion.content))
    # Always attach/refresh template workflow unless caller already customized.
    if plan.workflow is None or plan.workflow.preset != "custom":
        plan = plan.model_copy(
            update={"workflow": workflow_from_preset(workflow_preset, goal=plan.goal)}
        )
    return plan.model_copy(update={"sop": build_team_sop(plan)})


async def revise_team_plan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    current_plan: TeamPlan,
    requirement: str,
    feedback: str,
    scope: Literal["members", "workflow", "both"] = "both",
) -> TeamPlan:
    note = feedback.strip()
    if not note:
        raise TeamBuilderError("team_revise_feedback_invalid", "Feedback is required")
    try:
        model = await resolve_multi_agent_planning_model(db, tenant_id=tenant_id)
    except PlatformModelConfigurationError as exc:
        raise TeamBuilderError(
            "team_plan_model_unavailable", "Planning model is not configured", retryable=True
        ) from exc
    request = {
        "requirement": requirement,
        "revise_scope": scope,
        "feedback": note,
        "instruction": (
            "Revise the current team based on feedback. Prefer editing existing roles and stages "
            "instead of inventing an unrelated team. Update sop so the group announcement matches "
            "the final roster and workflow."
        ),
        "current_focus": _revision_focus(current_plan),
        "current_plan": current_plan.model_dump(mode="json"),
        "requesting_user": user.display_name,
    }
    try:
        with ai_interaction_scope(tenant_id=tenant_id, source="team_planning_revise"):
            completion = await complete_llm_once(
                model,
                [
                    LLMMessage(role="system", content=_REVISE_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=json.dumps(request, ensure_ascii=False)),
                ],
                tools=None,
                agent_id=None,
                supports_vision=False,
            )
    except Exception as exc:
        raise TeamBuilderError("team_plan_model_failed", "Team revise model call failed", retryable=True) from exc
    if completion.tool_calls:
        raise TeamBuilderError("team_plan_invalid", "Team revise model attempted to call a tool", retryable=True)
    revised = validate_team_plan(_parse_json(completion.content))
    if scope == "members":
        revised = revised.model_copy(
            update={
                "workflow": current_plan.workflow,
                "group_name": current_plan.group_name,
                "goal": current_plan.goal,
            }
        )
    elif scope == "workflow":
        workflow = revised.workflow or current_plan.workflow
        if workflow is not None and workflow.preset != "custom":
            workflow = workflow.model_copy(update={"preset": "custom"})
        revised = current_plan.model_copy(update={"workflow": workflow})
    elif revised.workflow is not None and revised.workflow.preset != "custom":
        # Feedback-driven edits should mark workflow as custom when stages diverge.
        if revised.workflow.model_dump() != (current_plan.workflow.model_dump() if current_plan.workflow else None):
            revised = revised.model_copy(
                update={"workflow": revised.workflow.model_copy(update={"preset": "custom"})}
            )
    # Keep announcement SOP aligned with the final roster/workflow after scoped merges.
    revised = revised.model_copy(update={"sop": build_team_sop(revised)})
    return revised
