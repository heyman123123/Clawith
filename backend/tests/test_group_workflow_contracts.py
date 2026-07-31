"""DAG workflow plans reject invalid task graphs before persistence."""

from __future__ import annotations

import uuid

import pytest

from app.services.group_workflow.contracts import GroupWorkflowPlanError, validate_workflow_plan


def _plan(*, build_dependencies: list[str] | None = None, accept_dependencies: list[str] | None = None) -> dict:
    return {
        "name": "交付工作流",
        "source": "ai",
        "stages": [
            {
                "key": "build",
                "title": "实现",
                "goal": "完成实现",
                "acceptance_criteria": ["实现范围已拆解"],
                "items": [
                    {
                        "item_key": "api",
                        "title": "接口实现",
                        "description": "实现接口",
                        "acceptance_criteria": ["接口测试通过"],
                        "depends_on": build_dependencies or [],
                    },
                    {
                        "item_key": "web",
                        "title": "前端实现",
                        "description": "实现页面",
                        "acceptance_criteria": ["页面可运行"],
                    },
                ],
            },
            {
                "key": "accept",
                "title": "验收",
                "goal": "完成验收",
                "acceptance_criteria": ["全部交付可验证"],
                "items": [
                    {
                        "item_key": "integration",
                        "title": "集成验收",
                        "description": "完成联调",
                        "acceptance_criteria": ["端到端测试通过"],
                        "depends_on": accept_dependencies or ["build.api", "build.web"],
                    }
                ],
            },
        ],
    }


def test_plan_accepts_explicit_cross_stage_dependencies() -> None:
    plan = validate_workflow_plan(_plan())

    assert plan.stages[1].items[0].depends_on == ["build.api", "build.web"]


@pytest.mark.parametrize(
    ("build_dependencies", "accept_dependencies", "message"),
    [
        (["build.api"], None, "itself"),
        (["accept.integration"], None, "later"),
        (None, ["build.missing"], "unknown"),
        (None, ["accept.integration"], "itself"),
        (None, ["build.api", "build.api"], "duplicate"),
    ],
)
def test_plan_rejects_invalid_dependencies(
    build_dependencies: list[str] | None,
    accept_dependencies: list[str] | None,
    message: str,
) -> None:
    with pytest.raises(GroupWorkflowPlanError, match=message):
        validate_workflow_plan(_plan(build_dependencies=build_dependencies, accept_dependencies=accept_dependencies))


def test_plan_rejects_dependency_cycle_within_stage() -> None:
    value = _plan()
    value["stages"][0]["items"][0]["depends_on"] = ["build.web"]
    value["stages"][0]["items"][1]["depends_on"] = ["build.api"]

    with pytest.raises(GroupWorkflowPlanError, match="cycle"):
        validate_workflow_plan(value)


def test_plan_requires_task_acceptance_criteria() -> None:
    value = _plan()
    del value["stages"][0]["items"][0]["acceptance_criteria"]

    with pytest.raises(GroupWorkflowPlanError, match="acceptance_criteria"):
        validate_workflow_plan(value)


def test_plan_rejects_participant_outside_group() -> None:
    value = _plan()
    value["stages"][0]["items"][0]["assignee_participant_id"] = str(uuid.uuid4())

    with pytest.raises(GroupWorkflowPlanError, match="outside the group"):
        validate_workflow_plan(value, participant_ids={uuid.uuid4()})


def test_task_dag_models_expose_persistent_graph_and_change_request_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG", "false")
    from app.models.group_workflow import (
        GroupWorkflowChangeRequest,
        GroupWorkflowItem,
        GroupWorkflowTaskDependency,
    )

    assert {"acceptance_criteria", "started_at", "completed_at", "failed_at", "failure_code", "failure_summary"} <= set(
        GroupWorkflowItem.__table__.columns.keys()
    )
    assert GroupWorkflowTaskDependency.__tablename__ == "group_workflow_task_dependencies"
    assert GroupWorkflowChangeRequest.__tablename__ == "group_workflow_change_requests"
    item_check = next(
        constraint for constraint in GroupWorkflowItem.__table__.constraints
        if constraint.name == "ck_group_workflow_items_status"
    )
    assert "ready" in str(item_check.sqltext)
    assert "failed" in str(item_check.sqltext)


@pytest.mark.parametrize("kind", ["default", "agile", "product_research"])
def test_preset_workflows_emit_evidence_gated_dependency_chains(kind: str) -> None:
    from app.services.group_workflow.templates import preset_workflow

    plan = preset_workflow(kind, goal="交付可验证成果")

    for position, stage in enumerate(plan.stages):
        assert stage.items
        for item in stage.items:
            assert item.acceptance_criteria
            if position == 0:
                assert item.depends_on == []
            else:
                assert item.depends_on
                assert all(dependency.startswith(f"{plan.stages[position - 1].key}.") for dependency in item.depends_on)
