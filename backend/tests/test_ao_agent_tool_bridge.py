"""Unit tests for AO agent tool bridge (需求 §4.1 / gap-closure W1)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.ao import agent_tool_bridge as bridge


@pytest.mark.asyncio
async def test_scheduler_tool_denied_for_quality_agent():
    agent_id = uuid.uuid4()

    class _DB:
        async def scalar(self, statement):
            sql = str(statement)
            if "agents" in sql.lower() or "FROM agents" in sql:
                return SimpleNamespace(
                    id=agent_id,
                    role_description="workflow.quality",
                    deleted_at=None,
                )
            return None

    result = await bridge.invoke_ao_tool(
        "ao_parse_workflow",
        {"workflow_id": str(uuid.uuid4())},
        agent_id=agent_id,
        db=_DB(),  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert result["error_code"] == "role_boundary_violation"
    assert result["required_role"] == "scheduler"


@pytest.mark.asyncio
async def test_quality_get_rules_allowed(monkeypatch):
    agent_id = uuid.uuid4()

    class _DB:
        async def scalar(self, statement):
            return SimpleNamespace(
                id=agent_id,
                role_description="workflow.quality",
                deleted_at=None,
            )

    result = await bridge.invoke_ao_tool(
        "get_quality_rules",
        {},
        agent_id=agent_id,
        db=_DB(),  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    assert isinstance(result["result"], list)
    assert any(r["key"] == "min_length" for r in result["result"])


@pytest.mark.asyncio
async def test_init_workflow_dir_for_scheduler(tmp_path, monkeypatch):
    agent_id = uuid.uuid4()
    workflow_id = str(uuid.uuid4())

    class _DB:
        async def scalar(self, statement):
            return SimpleNamespace(
                id=agent_id,
                role_description="workflow.scheduler",
                deleted_at=None,
            )

    monkeypatch.setenv("AO_OUTPUT_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.ao.scheduler_tools.get_settings",
        get_settings,
    )
    # scheduler_tools uses module-level helpers reading settings — patch output dir via ao_paths pattern
    import app.services.ao.scheduler_tools as st

    monkeypatch.setattr(st, "_workflow_run_dir", lambda wid: tmp_path / wid)

    result = await bridge.invoke_ao_tool(
        "init_workflow_dir",
        {"workflow_id": workflow_id},
        agent_id=agent_id,
        db=_DB(),  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    assert (tmp_path / workflow_id / "00-工作流定义" / "README.md").is_file()
    assert len(result["result"]["buckets"]) == 8


def test_ao_tool_names_exclude_send_channel_message():
    assert "ao_parse_workflow" in bridge.AO_TOOL_NAMES
    assert "quality_check_step" in bridge.AO_TOOL_NAMES
    assert "compile_delivery_package" in bridge.AO_TOOL_NAMES
    assert "send_channel_message" not in bridge.AO_TOOL_NAMES


def test_ao_builtin_definitions_cover_role_tools():
    from app.services.ao.ao_builtin_tools import AO_BUILTIN_TOOL_DEFINITIONS
    from app.services.workflow_role_seeder import SYSTEM_ROLES

    names = {d["name"] for d in AO_BUILTIN_TOOL_DEFINITIONS}
    for role_key, spec in SYSTEM_ROLES.items():
        for tool in spec.default_tools:
            if tool == "send_channel_message":
                continue
            assert tool in names, f"{role_key} missing builtin def for {tool}"
