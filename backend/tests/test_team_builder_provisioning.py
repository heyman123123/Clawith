"""Regression tests for durable team provisioning."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.team_builder import provisioning


@pytest.mark.asyncio
async def test_activation_message_is_persisted_before_job_references_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid an autoflush FK violation while enqueueing the activation message."""
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status="creating_group",
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        leader_participant_id=uuid.uuid4(),
        activation_message_id=None,
        error_code="old_error",
        error_message="old error",
    )
    user = SimpleNamespace(id=uuid.uuid4(), display_name="Requester", avatar_url=None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: job)),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        provisioning,
        "_load_scope",
        AsyncMock(return_value=(SimpleNamespace(reviewed_plan={}), user, [])),
    )
    monkeypatch.setattr(
        provisioning,
        "_resolve_members",
        AsyncMock(return_value=([job.leader_participant_id], job.leader_participant_id)),
    )
    monkeypatch.setattr(
        provisioning,
        "get_or_create_user_participant",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    )

    activation_message_id = uuid.uuid4()

    async def fake_enqueue(*_args: object, **kwargs: object) -> SimpleNamespace:
        assert job.activation_message_id is None
        assert kwargs["message_id"] is not None
        return SimpleNamespace(
            message=SimpleNamespace(id=activation_message_id),
            run_handles=(SimpleNamespace(),),
        )

    monkeypatch.setattr(provisioning.group_message_service, "enqueue_group_message", fake_enqueue)

    result = await provisioning.provision_job(db, job_id=job.id)

    assert result is job
    assert job.status == "completed"
    assert job.activation_message_id == activation_message_id
    assert job.error_code is None
    assert job.error_message is None
