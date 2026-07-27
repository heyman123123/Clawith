import pytest

from app.services.project_team_builder import HRPlanningError, parse_hr_team_plan, validate_team_plan


def _role(**overrides) -> dict:
    base = {
        "name": "Role",
        "duties": "Deliver outcomes",
        "soul": "# Role\nYou deliver outcomes.",
        "is_group_leader": False,
        "suggested_tools": ["group_write_workspace_file"],
        "suggested_permissions": {"scope_type": "company", "access_level": "use"},
    }
    base.update(overrides)
    return base


def test_hr_plan_is_derived_from_its_response_not_a_builtin_template() -> None:
    plan = parse_hr_team_plan(
        name="品牌快闪活动",
        requirements="在两周内完成线下快闪活动的策划、执行和复盘。",
        response_text='''{"roles":[
          {"name":"品牌主理人","duties":"统筹活动并向用户汇报","soul":"# 品牌主理人\\nYou lead the campaign.","is_group_leader":true,
           "suggested_tools":["group_write_workspace_file"],"suggested_permissions":{"scope_type":"company","access_level":"use"}},
          {"name":"线下活动执行","duties":"负责场地、供应商和现场执行","soul":"# 线下活动执行\\nYou run logistics.","is_group_leader":false,
           "suggested_tools":["group_write_workspace_file"],"suggested_permissions":{"scope_type":"company","access_level":"use"}},
          {"name":"品牌内容策划","duties":"负责传播主题和内容素材","soul":"# 品牌内容策划\\nYou craft content.","is_group_leader":false,
           "suggested_tools":["group_write_workspace_file"],"suggested_permissions":{"scope_type":"company","access_level":"use"}}
        ]}''',
    )
    assert plan["planner_name"] == "HR 招聘 Agent"
    assert [role["name"] for role in plan["roles"]] == ["品牌主理人", "线下活动执行", "品牌内容策划"]
    assert sum(role["is_group_leader"] for role in plan["roles"]) == 1
    assert all(role["duties"] for role in plan["roles"])
    assert all(role["soul"].startswith("#") for role in plan["roles"])
    assert "@品牌主理人" in plan["wake_up_message"]
    assert "负责场地、供应商和现场执行" in plan["wake_up_message"]
    assert "@线下活动执行" in plan["wake_up_message"]


def test_confirmed_plan_requires_one_and_only_one_group_leader() -> None:
    plan = {"roles": [
        _role(key="leader", name="Founder", duties="Coordinate", is_group_leader=True),
        _role(key="writer", name="Writer", duties="Write"),
    ]}
    for role in plan["roles"]:
        role["is_group_leader"] = False
    with pytest.raises(ValueError, match="exactly one group leader"):
        validate_team_plan(plan)

    plan["roles"][0]["is_group_leader"] = True
    plan["roles"][1]["is_group_leader"] = True
    with pytest.raises(ValueError, match="exactly one group leader"):
        validate_team_plan(plan)


def test_hr_response_without_json_is_rejected() -> None:
    with pytest.raises(HRPlanningError):
        parse_hr_team_plan(name="Launch", requirements="Ship it", response_text="team: leader")
