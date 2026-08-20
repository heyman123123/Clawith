"""Smoke test for g007: chat_sessions extended with chief_run_id."""
import pytest
from sqlalchemy import inspect
from app.database import engine


@pytest.mark.asyncio
async def test_chat_sessions_has_chief_run_id_column():
    async with engine.begin() as conn:
        cols = await conn.run_sync(
            lambda c: {r["name"] for r in inspect(c).get_columns("chat_sessions")}
        )
    assert "chief_run_id" in cols, "chief_run_id column required for Chief 1:1 Chat"
