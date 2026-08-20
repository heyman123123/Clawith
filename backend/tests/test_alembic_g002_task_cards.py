"""Smoke test for g002 migration: task_cards table + critical columns."""
import pytest
from sqlalchemy import inspect
from app.database import engine


@pytest.mark.asyncio
async def test_task_cards_table_exists_with_required_columns():
    async with engine.begin() as conn:
        cols = await conn.run_sync(
            lambda c: {r["name"] for r in inspect(c).get_columns("task_cards")}
        )
    assert "tenant_id" in cols
    assert "version" in cols, "optimistic lock column required"
    assert "column" in cols, "kanban column required"
