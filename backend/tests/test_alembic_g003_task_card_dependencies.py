"""Smoke test for g003 migration: task_card_dependencies table."""
import pytest
from sqlalchemy import inspect
from app.database import engine


@pytest.mark.asyncio
async def test_task_card_dependencies_table_exists():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "task_card_dependencies" in tables
