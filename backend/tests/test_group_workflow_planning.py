"""Generated plans stay drafts until the user explicitly confirms them."""

import uuid
from types import SimpleNamespace

import pytest

from app.services.group_workflow.planning import GroupWorkflowPlanningError, confirmed_plan


def test_confirmed_plan_rejects_unready_draft() -> None:
    with pytest.raises(GroupWorkflowPlanningError, match="not ready"):
        confirmed_plan(SimpleNamespace(status="generating", plan=None))


def test_confirmed_plan_keeps_ai_source() -> None:
    participant_id = uuid.uuid4()
    draft = SimpleNamespace(
        status="ready",
        plan={
            "name": "AI plan", "source": "ai",
            "stages": [{"key": "clarify", "title": "澄清", "goal": "确认范围", "items": [{"item_key": "brief", "title": "产出简报", "description": "形成简报", "assignee_participant_id": str(participant_id)}]}],
        },
    )

    assert confirmed_plan(draft).source == "ai"
