"""Unit tests for attaching team_building HR sessions to existing chat sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.hr_review import HrReviewSession
from app.services.hr_review_board_seeder import HR_REVIEW_BOARD_GROUP_TYPE
from app.services.hr_review_session_service import HrReviewError, attach_team_building_session

NOW = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)


def _hr_group(tenant_id: uuid.UUID, participant_id: uuid.UUID) -> Group:
    return Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="HR 评审群",
        description=None,
        group_type=HR_REVIEW_BOARD_GROUP_TYPE,
        created_by_participant_id=participant_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _chat_session(tenant_id: uuid.UUID, group_id: uuid.UUID, participant_id: uuid.UUID) -> ChatSession:
    return ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        agent_id=None,
        user_id=None,
        created_by_participant_id=participant_id,
        title="新需求",
        source_channel="web",
        is_primary=False,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_attach_team_building_session_creates_row_for_hr_group() -> None:
    tenant_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    group = _hr_group(tenant_id, participant_id)
    chat_session = _chat_session(tenant_id, group.id, participant_id)
    chat_session.title = "Inventory App"
    added: list[object] = []

    class _DB:
        async def scalar(self, _query):
            return None

        async def get(self, model, key):
            if model is ChatSession and key == chat_session.id:
                return chat_session
            if model is Group and key == group.id:
                return group
            return None

        def add(self, item):
            added.append(item)

        async def flush(self):
            return None

    hr_session = await attach_team_building_session(
        _DB(),
        tenant_id=tenant_id,
        chat_session_id=chat_session.id,
    )

    assert isinstance(hr_session, HrReviewSession)
    assert hr_session.group_id == group.id
    assert hr_session.session_id == chat_session.id
    assert hr_session.session_type == "team_building"
    assert hr_session.status == "open"
    assert hr_session.proposals == []
    assert hr_session.context_payload == {"name": "Inventory App"}
    assert added == [hr_session]


@pytest.mark.asyncio
async def test_attach_team_building_session_is_idempotent() -> None:
    tenant_id = uuid.uuid4()
    existing = HrReviewSession(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        session_type="team_building",
        status="open",
        proposals=[],
        context_payload={},
        created_at=NOW,
    )

    class _DB:
        async def scalar(self, _query):
            return existing

    result = await attach_team_building_session(
        _DB(),
        tenant_id=tenant_id,
        chat_session_id=existing.session_id,
    )
    assert result is existing


@pytest.mark.asyncio
async def test_attach_team_building_session_rejects_non_hr_group() -> None:
    tenant_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    group = Group(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="普通群",
        description=None,
        group_type=None,
        created_by_participant_id=participant_id,
        created_at=NOW,
        updated_at=NOW,
    )
    chat_session = _chat_session(tenant_id, group.id, participant_id)

    class _DB:
        async def scalar(self, _query):
            return None

        async def get(self, model, key):
            if model is ChatSession and key == chat_session.id:
                return chat_session
            if model is Group and key == group.id:
                return group
            return None

    with pytest.raises(HrReviewError, match="HR 评审群"):
        await attach_team_building_session(
            _DB(),
            tenant_id=tenant_id,
            chat_session_id=chat_session.id,
        )
