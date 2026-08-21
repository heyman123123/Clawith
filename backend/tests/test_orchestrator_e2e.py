"""T7.1: Smoke E2E test for the orchestrator flow (no real LLM).

Steps:
  1. create Draft directly in DB
  2. approve it
  3. AtomicCreator with mocked services materializes it
  4. Verify idempotency: re-running AtomicCreator returns same result

Uses the real DB (drafts table) but mocks downstream services to avoid
hitting Groups / Agents / OKR / TaskBoard (which require fuller env wiring).
"""
from unittest.mock import AsyncMock, MagicMock
import pytest
import uuid

import app.models.llm  # noqa: F401  -- register for FK resolution
from app.database import async_session
from app.models.draft import Draft as DraftModel
from app.services.orchestrator.atomic_creator import AtomicCreator


@pytest.mark.asyncio
async def test_atomic_creator_smoke():
    """T7.1: full draft->approve->materialize flow with mocked side services."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    draft_id = uuid.uuid4()

    # Step 1: Create a pre-approved Draft directly in DB
    async with async_session() as db:
        draft = DraftModel(
            id=draft_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_message="test goal",
            intent_summary={"intent_category": "ops", "scope_summary": "x", "key_constraints": []},
            draft_payload={
                "group": {"name": "test", "description": "test group"},
                "members": [{"role": "researcher", "name": "R", "system_prompt": "x"}],
                "okr": {"objective_title": "O", "objective_description": "D", "key_results": []},
                "tasks": [{"title": "task 1", "description": "d", "assignee_role": "researcher"}],
            },
            template_visibility="user_private",
            status="approved",
        )
        db.add(draft)
        await db.commit()

    # Step 2: AtomicCreator with mocked downstream services
    mock_group = MagicMock()
    mock_group.create = AsyncMock()
    mock_agent = MagicMock()
    mock_agent.create = AsyncMock()
    mock_okr = MagicMock()
    mock_okr.create_objective = AsyncMock()
    mock_tb = MagicMock()
    mock_tb.create_initial_cards = AsyncMock(return_value=[uuid.uuid4()])
    mock_ci = MagicMock()
    mock_ci.enqueue_start_chief_run = AsyncMock()

    creator = AtomicCreator(
        group_service=mock_group,
        agent_service=mock_agent,
        okr_service=mock_okr,
        task_board_service=mock_tb,
        command_intake_service=mock_ci,
    )

    # Step 3: Materialize
    result = await creator.create(tenant_id=tenant_id, draft_id=draft_id)
    assert "group_id" in result
    assert "chief_run_id" in result
    assert "task_card_ids" in result
    # Verify downstream services were called
    mock_group.create.assert_awaited_once()
    mock_ci.enqueue_start_chief_run.assert_awaited_once()

    # Step 4: Verify Draft marked consumed
    async with async_session() as db:
        row = await db.get(DraftModel, draft_id)
        assert row.status == "consumed"

    # Step 5: Idempotency — re-running returns same group_id (replay)
    result2 = await creator.create(tenant_id=tenant_id, draft_id=draft_id)
    assert result2["group_id"] == result["group_id"]
    assert result2["chief_run_id"] == result["chief_run_id"]
    # Downstream services NOT called again
    mock_group.create.assert_awaited_once()  # still 1, not 2
