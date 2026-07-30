"""API regressions for async ORM serialization in the team-builder flow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import team_builder as team_builder_api
from app.models.team_builder import TeamBuildDraft


@pytest.mark.asyncio
async def test_draft_response_refreshes_server_managed_timestamps_before_serializing() -> None:
    now = datetime.now(UTC)
    draft = TeamBuildDraft(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        creator_user_id=uuid.uuid4(),
        requirement="Create a research team",
        constraints={},
        status="ready",
        plan_version=1,
        created_at=now,
        updated_at=now,
    )
    db = SimpleNamespace(refresh=AsyncMock())

    result = await team_builder_api._draft_out(db, draft)

    db.refresh.assert_awaited_once_with(draft)
    assert result.id == draft.id
    assert result.updated_at == now


def test_history_endpoint_precedes_the_dynamic_draft_route() -> None:
    paths = [route.path for route in team_builder_api.router.routes]

    assert paths.index("/api/team-build-drafts") < paths.index("/api/team-build-drafts/{draft_id}")
    assert set(team_builder_api.TeamProvisionJobSummaryOut.model_fields) >= {
        "status", "group_id", "session_id",
    }
