"""Smoke test for g006: agent_templates extended with visibility + approval fields."""
import pytest
from sqlalchemy import inspect
from app.database import engine


@pytest.mark.asyncio
async def test_agent_templates_has_visibility_and_approval_columns():
    async with engine.begin() as conn:
        cols = await conn.run_sync(
            lambda c: {r["name"] for r in inspect(c).get_columns("agent_templates")}
        )
    assert "template_visibility" in cols
    assert "approval_state" in cols
    assert "tenant_id" in cols
