"""Smoke test for g001 migration: drafts table must exist after upgrade."""
import pytest
from sqlalchemy import inspect
from app.database import engine


@pytest.mark.asyncio
async def test_drafts_table_exists():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "drafts" in tables, "drafts table should exist after g001 migration"
