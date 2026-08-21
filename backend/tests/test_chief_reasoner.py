"""T4.4: Reasoner LLM call + parse + safe fallback."""
import pytest
from app.services.agent_runtime.orchestrator.reasoner import Reasoner
from app.services.agent_runtime.orchestrator.checkpoint_state import (
    ActionPlan, GroupState, CardMoveAction,
)


@pytest.mark.asyncio
async def test_reasoner_parses_valid_action_plan():
    async def mock_llm(prompt):
        return '{"review_artifact_paths": ["a.md"], "move_cards": [{"card_id": "00000000-0000-0000-0000-000000000001", "to_column": "done", "expected_version": 0}]}'

    r = Reasoner(llm_caller=mock_llm)
    state = GroupState(group_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000002"))
    event = type("E", (), {"event_type": "card_done"})
    plan = await r.decide(event=event, group_state=state)
    assert len(plan.review_artifact_paths) == 1
    assert len(plan.move_cards) == 1
    assert plan.move_cards[0].to_column == "done"


@pytest.mark.asyncio
async def test_reasoner_strips_code_fences():
    async def mock_llm(prompt):
        return '```json\n{"notify_user": "hi"}\n```'
    r = Reasoner(llm_caller=mock_llm)
    plan = await r.decide(event=None, group_state=GroupState(group_id=__import__("uuid").uuid4()))
    assert plan.notify_user == "hi"


@pytest.mark.asyncio
async def test_reasoner_safe_noop_on_parse_failure():
    async def mock_llm(prompt):
        return "garbage"
    r = Reasoner(llm_caller=mock_llm)
    plan = await r.decide(event=None, group_state=GroupState(group_id=__import__("uuid").uuid4()))
    assert plan == ActionPlan()  # empty default


@pytest.mark.asyncio
async def test_reasoner_falls_back_when_primary_fails():
    calls = []
    async def fail(p):
        calls.append("primary")
        raise RuntimeError("primary down")
    async def ok(p):
        calls.append("fallback")
        return '{"notify_user": "from fallback"}'
    r = Reasoner(llm_caller=fail, fallback_caller=ok)
    plan = await r.decide(event=None, group_state=GroupState(group_id=__import__("uuid").uuid4()))
    assert calls == ["primary", "fallback"]
    assert plan.notify_user == "from fallback"


@pytest.mark.asyncio
async def test_reasoner_safe_noop_when_llm_fails_with_no_fallback():
    async def fail(p):
        raise RuntimeError("primary down")
    r = Reasoner(llm_caller=fail)
    plan = await r.decide(event=None, group_state=GroupState(group_id=__import__("uuid").uuid4()))
    assert plan == ActionPlan()
