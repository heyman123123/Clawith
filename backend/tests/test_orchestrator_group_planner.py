import pytest
import uuid
from app.services.orchestrator.group_planner import GroupPlanner
from app.services.orchestrator.draft_schemas import AgentProposal, DraftSummary


@pytest.mark.asyncio
async def test_planner_emits_group_okr_tasks():
    p = GroupPlanner()
    members = [
        AgentProposal(role="researcher", name="R"),
        AgentProposal(role="chief-of-staff", name="Chief"),
    ]
    summary = DraftSummary(
        intent_category="growth_strategy", scope_summary="获客方案", key_constraints=[]
    )
    group, okr, tasks = await p.plan(
        summary=summary, members=members, user_message="帮我做获客方案"
    )
    assert "获客" in group.name or "新项目" in group.name
    assert okr is not None
    assert len(okr.key_results) >= 1
    # Chief should NOT get a task (chief doesn't execute)
    assert all(t.assignee_role != "chief-of-staff" for t in tasks)
    # Researcher should get one
    assert any(t.assignee_role == "researcher" for t in tasks)
