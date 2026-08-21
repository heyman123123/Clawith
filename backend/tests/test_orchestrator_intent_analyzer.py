import pytest
from app.services.orchestrator.intent_analyzer import IntentAnalyzer


@pytest.mark.asyncio
async def test_analyzer_parses_valid_json():
    async def mock_llm(prompt):
        return '{"intent_category": "growth_strategy", "scope_summary": "出海 SaaS 获客", "key_constraints": ["6周"]}'

    a = IntentAnalyzer(llm_caller=mock_llm)
    s = await a.analyze("帮我做出海 SaaS 获客方案", tenant_id=None, user_id=None)
    assert s.intent_category == "growth_strategy"
    assert "获客" in s.scope_summary


@pytest.mark.asyncio
async def test_analyzer_strips_code_fences():
    async def mock_llm(prompt):
        return '```json\n{"intent_category": "ops", "scope_summary": "x", "key_constraints": []}\n```'
    a = IntentAnalyzer(llm_caller=mock_llm)
    s = await a.analyze("test", tenant_id=None, user_id=None)
    assert s.intent_category == "ops"


@pytest.mark.asyncio
async def test_analyzer_falls_back_on_parse_failure():
    async def mock_llm(prompt):
        return "totally not JSON"
    a = IntentAnalyzer(llm_caller=mock_llm)
    s = await a.analyze("test", tenant_id=None, user_id=None)
    assert s.intent_category == "other"
    assert "totally not JSON" in s.scope_summary


@pytest.mark.asyncio
async def test_analyzer_uses_fallback_when_primary_fails():
    calls = []

    async def failing(prompt):
        calls.append("primary")
        raise RuntimeError("primary down")

    async def ok_fallback(prompt):
        calls.append("fallback")
        return '{"intent_category": "eng", "scope_summary": "fb", "key_constraints": []}'

    a = IntentAnalyzer(llm_caller=failing, fallback_caller=ok_fallback)
    s = await a.analyze("test", tenant_id=None, user_id=None)
    assert s.intent_category == "eng"
    assert calls == ["primary", "fallback"]
