"""T4.6: start_chief_run handler smoke tests."""
from unittest.mock import AsyncMock, MagicMock
import pytest
import uuid
from app.services.agent_runtime.orchestrator.command_handler import (
    handle_start_chief_run,
    get_running_loops,
)


@pytest.mark.asyncio
async def test_handler_requires_group_id():
    with pytest.raises(KeyError):
        await handle_start_chief_run({"tenant_id": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_get_running_loops_returns_dict():
    assert isinstance(get_running_loops(), dict)


def test_handler_module_exports():
    import app.services.agent_runtime.orchestrator.command_handler as m
    assert callable(m.handle_start_chief_run)
    assert callable(m.get_running_loops)
