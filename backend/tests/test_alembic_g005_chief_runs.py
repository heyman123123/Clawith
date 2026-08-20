"""Smoke test for g005: chief_runs table with 1:1 group relationship."""
import pytest
from sqlalchemy import inspect
from app.database import engine


@pytest.mark.asyncio
async def test_chief_runs_table_exists_with_unique_group():
    async with engine.begin() as conn:
        constraints = await conn.run_sync(
            lambda c: [u["name"] for u in inspect(c).get_unique_constraints("chief_runs")]
        )
    assert "uq_chief_runs_group_id" in constraints, "group_id must be UNIQUE (1:1 with groups)"
