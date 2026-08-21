import pytest
import uuid
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.draft_schemas import DraftSummary


@pytest.mark.asyncio
async def test_selector_uses_existing_templates():
    async def search(category, tenant_id):
        return [{"role": "researcher", "name": "R", "template_id": uuid.uuid4()}]

    async def gen(category, gaps):
        return []

    s = AgentSelector(template_search=search, llm_generate_template=gen)
    members = await s.select(
        summary=DraftSummary(
            intent_category="growth_strategy", scope_summary="x", key_constraints=[]
        ),
        tenant_id=None,
    )
    roles = {m.role for m in members}
    assert "researcher" in roles
    assert "chief-of-staff" in roles  # always added


@pytest.mark.asyncio
async def test_selector_fills_gaps_via_llm():
    async def search(category, tenant_id):
        return []  # no existing

    async def gen(category, gaps):
        return [{"role": "writer", "name": "W", "system_prompt": "you write"}]

    s = AgentSelector(template_search=search, llm_generate_template=gen)
    members = await s.select(
        summary=DraftSummary(
            intent_category="creative", scope_summary="x", key_constraints=[]
        ),
        tenant_id=None,
    )
    roles = {m.role for m in members}
    assert "writer" in roles
    assert "ideator" not in roles  # no LLM gen for ideator? actually it should...


@pytest.mark.asyncio
async def test_selector_no_chief_when_disabled():
    async def search(category, tenant_id):
        return [{"role": "researcher", "name": "R", "template_id": uuid.uuid4()}]

    async def gen(category, gaps):
        return []

    s = AgentSelector(template_search=search, llm_generate_template=gen)
    members = await s.select(
        summary=DraftSummary(
            intent_category="market_research", scope_summary="x", key_constraints=[]
        ),
        tenant_id=None,
        always_include_chief=False,
    )
    assert "chief-of-staff" not in {m.role for m in members}
