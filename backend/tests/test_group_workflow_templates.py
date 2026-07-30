"""Contract tests for the workflow templates and generated-plan validation."""

from __future__ import annotations

import pytest

from app.services.group_workflow.contracts import GroupWorkflowPlanError, validate_workflow_plan
from app.services.group_workflow.templates import preset_workflow


def test_agile_template_has_ordered_delivery_stages_and_acceptance_gate() -> None:
    plan = preset_workflow("agile", goal="发布搜索")

    assert [stage.key for stage in plan.stages] == ["clarify", "backlog", "plan", "build", "accept", "retro"]
    assert plan.stages[4].requires_approval is True


def test_product_research_template_has_release_gate() -> None:
    plan = preset_workflow("product_research", goal="改版产品")

    assert plan.source == "product_research"
    assert plan.stages[-1].key == "release"
    assert plan.stages[-1].requires_approval is True


def test_plan_rejects_duplicate_stage_keys() -> None:
    with pytest.raises(GroupWorkflowPlanError, match="stage keys must be unique"):
        validate_workflow_plan({
            "name": "Broken", "source": "ai",
            "stages": [
                {"key": "same", "title": "One", "goal": "One", "items": [{"item_key": "one", "title": "One", "description": "One"}]},
                {"key": "same", "title": "Two", "goal": "Two", "items": [{"item_key": "two", "title": "Two", "description": "Two"}]},
            ],
        })
