"""Cross-tenant denial tests for HR review API helpers."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.hr_review_session_service import (
    HrReviewError,
    generate_team_building_proposals,
    get_hr_session_by_chat_for_tenant,
    get_hr_session_for_tenant,
    select_proposal,
)


@pytest.mark.asyncio
async def test_get_hr_session_for_tenant_returns_none_for_other_tenant() -> None:
    class _DB:
        async def scalar(self, _query):
            return None

    result = await get_hr_session_for_tenant(
        _DB(),
        hr_session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_hr_session_by_chat_for_tenant_returns_none_for_other_tenant() -> None:
    class _DB:
        async def scalar(self, _query):
            return None

    result = await get_hr_session_by_chat_for_tenant(
        _DB(),
        chat_session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_generate_team_building_proposals_denies_cross_tenant() -> None:
    class _DB:
        async def scalar(self, _query):
            return None

    with pytest.raises(HrReviewError, match="不存在"):
        await generate_team_building_proposals(
            _DB(),
            hr_session_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            creator_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_select_proposal_denies_cross_tenant(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.hr_review_session_service.get_hr_session_for_tenant",
        AsyncMock(return_value=None),
    )
    from types import SimpleNamespace

    user = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        display_name="Attacker",
        avatar_url=None,
    )
    with pytest.raises(HrReviewError, match="不存在"):
        await select_proposal(
            object(),
            hr_session_id=uuid.uuid4(),
            proposal_id="proposal_1",
            user=user,
        )


def test_hr_api_joins_group_for_tenant_scope() -> None:
    from pathlib import Path

    api_source = Path("app/api/hr_review.py").read_text()
    service_source = Path("app/services/hr_review_session_service.py").read_text()
    assert "get_hr_session_for_tenant" in api_source
    assert "get_hr_session_by_chat_for_tenant" in api_source
    assert ".join(Group, Group.id == HrReviewSession.group_id)" in service_source
    assert "Group.tenant_id == tenant_id" in service_source
