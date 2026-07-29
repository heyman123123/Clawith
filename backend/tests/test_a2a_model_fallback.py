from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent_runtime.a2a_runtime import A2ARuntimeError, _resolve_target_model_id


@pytest.mark.asyncio
async def test_a2a_resolve_uses_primary_when_present():
    primary_id = uuid.uuid4()
    agent = SimpleNamespace(name="Ops", primary_model_id=primary_id)
    assert await _resolve_target_model_id(AsyncMock(), agent) == primary_id


@pytest.mark.asyncio
async def test_a2a_resolve_falls_back_to_tenant_default():
    default_id = uuid.uuid4()
    agent = SimpleNamespace(name="Ops", primary_model_id=None)
    with patch(
        "app.services.agent_runtime.a2a_runtime.resolve_active_agent_model",
        new=AsyncMock(return_value=SimpleNamespace(id=default_id)),
    ):
        assert await _resolve_target_model_id(AsyncMock(), agent) == default_id


@pytest.mark.asyncio
async def test_a2a_resolve_raises_when_no_model_available():
    agent = SimpleNamespace(name="Ops", primary_model_id=None)
    with patch(
        "app.services.agent_runtime.a2a_runtime.resolve_active_agent_model",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(A2ARuntimeError, match="tenant default"):
            await _resolve_target_model_id(AsyncMock(), agent)
