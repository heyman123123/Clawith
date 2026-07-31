"""Pure contract tests for the pre-provisioning team plan."""

from __future__ import annotations

import uuid

import pytest

from app.services import agency_role_sync
from app.services.agency_role_sync import agency_role_template_root, import_agency_roles
from app.services.team_builder.errors import TeamBuilderError
from app.services.team_builder.planning import _role_catalog, fallback_team_plan, validate_team_plan


def test_agency_agents_zh_roles_use_a_persistent_runtime_cache() -> None:
    root = agency_role_template_root()

    assert "agent_templates" not in str(root)
    assert root.parent.name == "role_templates"
    assert root.name == "agency-agents-zh"


def test_agency_role_import_writes_to_runtime_cache(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "agency-agents-zh"
    role_file = source / "engineering" / "frontend.md"
    role_file.parent.mkdir(parents=True)
    (source / "LICENSE").write_text("MIT", encoding="utf-8")
    role_file.write_text(
        "---\nname: 前端开发者\ndescription: React 专家\nemoji: 💻\n---\n\n# Role\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agency_role_sync, "_MINIMUM_ROLE_COUNT", 1)
    output = tmp_path / "runtime" / "agency-agents-zh"

    imported = import_agency_roles(source, output)

    assert imported == 1
    assert (output / "agency-engineering-frontend" / "meta.yaml").is_file()
    assert "# Role" in (output / "agency-engineering-frontend" / "soul.md").read_text(encoding="utf-8")


def test_role_catalog_exposes_real_template_ids_to_the_team_planner() -> None:
    template_id = uuid.uuid4()
    catalog = _role_catalog([
        type("Template", (), {
            "id": template_id,
            "name": "前端开发者",
            "description": "React 和性能优化专家",
            "category": "engineering",
            "capability_bullets": ["React", "性能"],
        })(),
    ])

    assert catalog == [{
        "template_id": str(template_id),
        "name": "前端开发者",
        "description": "React 和性能优化专家",
        "category": "engineering",
        "capabilities": ["React", "性能"],
    }]


def test_fallback_plan_has_one_new_leader_and_public_delegation() -> None:
    plan = fallback_team_plan("准备一个产品发布")

    leaders = [member for member in plan.members if member.is_leader]
    assert len(leaders) == 1
    assert leaders[0].source == "new"
    assert plan.delegations[0].from_member_key == leaders[0].member_key
    assert plan.workflow is not None
    assert len(plan.workflow.stages) >= 2


def test_fallback_plan_includes_sop_announcement() -> None:
    plan = fallback_team_plan("准备一个产品发布", workflow_preset="agile")
    assert plan.sop is not None
    assert "协作 SOP" in plan.sop
    assert "全体 Agent 必须遵循" in plan.sop
    assert "at" in plan.sop
    assert plan.workflow is not None
    assert plan.workflow.stages[0].title in plan.sop


def test_build_team_sop_lists_roles_and_stages() -> None:
    from app.services.team_builder.planning import build_team_sop

    plan = fallback_team_plan("交付低代码平台")
    sop = build_team_sop(plan)
    assert "团队群主" in sop
    assert "交付专员" in sop
    assert "决策者" in sop



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
