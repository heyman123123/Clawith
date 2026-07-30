"""Pure contract tests for the pre-provisioning team plan."""

from __future__ import annotations

import uuid

import pytest

from app.services.team_builder.errors import TeamBuilderError
from app.services.team_builder.planning import fallback_team_plan, validate_team_plan


def test_fallback_plan_has_one_new_leader_and_public_delegation() -> None:
    plan = fallback_team_plan("准备一个产品发布")

    leaders = [member for member in plan.members if member.is_leader]
    assert len(leaders) == 1
    assert leaders[0].source == "new"
    assert plan.delegations[0].from_member_key == leaders[0].member_key
    assert plan.workflow is not None
    assert len(plan.workflow.stages) >= 2


def test_fallback_plan_respects_workflow_preset() -> None:
    plan = fallback_team_plan("准备一个产品发布", workflow_preset="agile")
    assert plan.workflow is not None
    assert plan.workflow.preset == "agile"


def test_validate_team_plan_attaches_default_workflow_when_missing() -> None:
    payload = fallback_team_plan("准备一个产品发布").model_dump(mode="json")
    del payload["workflow"]
    plan = validate_team_plan(payload)
    assert plan.workflow is not None
    assert plan.workflow.preset == "default"


def test_team_plan_rejects_multiple_leaders() -> None:
    agent_id = str(uuid.uuid4())
    payload = {
        "group_name": "Launch",
        "goal": "Ship",
        "assumptions": [],
        "phases": ["Plan"],
        "members": [
            {
                "member_key": "leader_one",
                "name": "Leader One",
                "role_description": "Lead",
                "responsibility": "Coordinate",
                "source": "existing",
                "existing_agent_id": agent_id,
                "template_id": None,
                "skill_ids": [],
                "is_leader": True,
            },
            {
                "member_key": "leader_two",
                "name": "Leader Two",
                "role_description": "Lead",
                "responsibility": "Coordinate",
                "source": "existing",
                "existing_agent_id": str(uuid.uuid4()),
                "template_id": None,
                "skill_ids": [],
                "is_leader": True,
            },
        ],
        "delegations": [],
    }

    with pytest.raises(TeamBuilderError, match="exactly one member"):
        validate_team_plan(payload)


def test_team_plan_rejects_existing_member_without_agent_identity() -> None:
    payload = fallback_team_plan("Prepare launch").model_dump(mode="json")
    payload["members"][0]["source"] = "existing"

    with pytest.raises(TeamBuilderError, match="existing members require"):
        validate_team_plan(payload)


def test_team_plan_normalizes_phase_objects_and_slug_ids() -> None:
    payload = {
        "group_name": "低代码团队",
        "goal": "搭建低代码平台",
        "assumptions": [{"text": "优先复用现有能力"}],
        "phases": [
            {"name": "阶段一：需求澄清", "deliverables": ["PRD"]},
            "阶段二：架构设计",
        ],
        "members": [
            {
                "member_key": "team_leader",
                "name": "项目经理",
                "role_description": "统筹",
                "responsibility": "拆解与分派",
                "source": "new",
                "template_id": "project_manager",
                "skill_ids": ["project_management", "task_decomposition"],
                "is_leader": True,
            },
            {
                "member_key": "frontend_engineer",
                "name": "前端工程师",
                "role_description": "前端实现",
                "responsibility": "页面与交互",
                "source": "new",
                "template_id": "frontend_engineer",
                "skill_ids": ["react", "typescript"],
                "is_leader": False,
            },
        ],
        "delegations": [
            {
                "from_member_key": "team_leader",
                "to_member_key": "frontend_engineer",
                "instruction": "完成首屏交互原型",
            }
        ],
    }

    plan = validate_team_plan(payload)

    assert plan.phases == ["阶段一：需求澄清", "阶段二：架构设计"]
    assert plan.assumptions == ["优先复用现有能力"]
    assert plan.members[0].template_id is None
    assert plan.members[0].skill_ids == []
    assert plan.members[1].template_id is None
    assert plan.members[1].skill_ids == []
