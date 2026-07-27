from __future__ import annotations

import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.tool import AgentTool
from app.services.project_team_builder import apply_suggested_tools, materialize_role_agent


def _role(**overrides):
    base = {
        "key": "pm",
        "name": "项目经理",
        "duties": "统筹交付",
        "soul": "# PM\nYou lead the group.",
        "is_group_leader": True,
        "suggested_tools": ["group_write_workspace_file"],
        "suggested_permissions": {"scope_type": "company", "access_level": "use"},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_apply_suggested_tools_warns_and_skips_unknown_tools(caplog):
    agent_id = uuid.uuid4()

    class _Session:
        async def scalar(self, _query):
            return None

        async def flush(self):
            return None

    with caplog.at_level(logging.WARNING, logger="app.services.project_team_builder"):
        await apply_suggested_tools(
            _Session(),
            agent_id=agent_id,
            tool_names=["nonexistent_tool_xyz", "another_fake_tool"],
        )

    warnings = [record.message for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("nonexistent_tool_xyz" in message for message in warnings)
    assert any("another_fake_tool" in message for message in warnings)
    assert all("not found" in message.lower() for message in warnings)


@pytest.mark.asyncio
async def test_apply_suggested_tools_enables_known_tools_and_warns_on_unknown(caplog):
    agent_id = uuid.uuid4()
    known_tool = SimpleNamespace(
        id=uuid.uuid4(),
        name="group_write_workspace_file",
        enabled=True,
    )
    added: list[object] = []
    scalar_calls = 0

    class _Session:
        def add(self, item):
            added.append(item)

        async def scalar(self, _query):
            nonlocal scalar_calls
            scalar_calls += 1
            if scalar_calls == 1:
                return known_tool
            return None

        async def flush(self):
            return None

    with caplog.at_level(logging.WARNING, logger="app.services.project_team_builder"):
        await apply_suggested_tools(
            _Session(),
            agent_id=agent_id,
            tool_names=["group_write_workspace_file", "bogus_tool_name"],
        )

    agent_tool_rows = [item for item in added if isinstance(item, AgentTool)]
    assert len(agent_tool_rows) == 1
    assert agent_tool_rows[0].agent_id == agent_id
    assert agent_tool_rows[0].tool_id == known_tool.id
    assert agent_tool_rows[0].enabled is True
    assert any("bogus_tool_name" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_materialize_role_agent_tolerates_unknown_suggested_tools(caplog):
    added: list[object] = []

    class _Session:
        def add_all(self, items):
            added.extend(items)

        def add(self, item):
            added.append(item)

        async def flush(self):
            return None

        async def scalar(self, _query):
            return None

    with caplog.at_level(logging.WARNING, logger="app.services.project_team_builder"):
        role, agent, participant = await materialize_role_agent(
            _Session(),
            tenant_id=uuid.uuid4(),
            creator_id=uuid.uuid4(),
            project_name="Demo",
            role=_role(suggested_tools=["bogus_tool_name"]),
            default_model_id=uuid.uuid4(),
            tenant=None,
        )

    assert role["soul"].startswith("# PM")
    assert agent.name == "项目经理"
    assert participant.display_name == "项目经理"
    assert any("bogus_tool_name" in record.message for record in caplog.records)
    assert not [item for item in added if isinstance(item, AgentTool)]


@pytest.mark.asyncio
async def test_materialize_role_agent_applies_permissions_and_tools(monkeypatch):
    added: list[object] = []

    class _Session:
        def add_all(self, items):
            added.extend(items)

        def add(self, item):
            added.append(item)

        async def flush(self):
            return None

        async def scalar(self, _query):
            return None

    monkeypatch.setattr(
        "app.services.project_team_builder.apply_suggested_tools",
        AsyncMock(),
    )

    role, agent, participant = await materialize_role_agent(
        _Session(),
        tenant_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        project_name="Demo",
        role=_role(),
        default_model_id=uuid.uuid4(),
        tenant=None,
    )
    assert role["soul"].startswith("# PM")
    assert agent.role_description == "统筹交付"
    assert participant.display_name == "项目经理"
    permission_rows = [item for item in added if getattr(item, "scope_type", None) == "company"]
    assert permission_rows


@pytest.mark.asyncio
async def test_select_proposal_returns_execution_redirect_fields(monkeypatch):
    from app.services.hr_review_session_service import select_proposal

    hr_session_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        display_name="Owner",
        avatar_url=None,
    )
    hr_session = SimpleNamespace(
        id=hr_session_id,
        session_type="team_building",
        status="open",
        group_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        proposals=[
            {
                "id": "proposal_1",
                "label": "方案 A",
                "card_summary": "摘要",
                "roles": [_role(), _role(key="dev", name="开发", is_group_leader=False)],
            }
        ],
        context_payload={"name": "Demo Project", "requirements": "Ship MVP"},
        selected_proposal_id=None,
        closed_at=None,
    )

    class _Session:
        async def flush(self):
            return None

    provisioned = {
        "roles": hr_session.proposals[0]["roles"],
        "wake_up_message": "@项目经理 kickoff",
        "project_name": "Demo Project",
        "requirements": "Ship MVP",
        "workflow_id": str(workflow_id),
        "group_id": str(group_id),
        "session_id": str(session_id),
    }

    class _ProvisioningError(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "app.services.project_provisioning",
        SimpleNamespace(
            ProjectProvisioningError=_ProvisioningError,
            provision_team_from_plan=AsyncMock(return_value=provisioned),
        ),
    )
    monkeypatch.setattr(
        "app.services.hr_review_session_service.get_hr_session_for_tenant",
        AsyncMock(return_value=hr_session),
    )
    monkeypatch.setattr(
        "app.services.hr_review_session_service._send_hr_selection_receipt",
        AsyncMock(),
    )

    result = await select_proposal(
        _Session(),
        hr_session_id=hr_session_id,
        proposal_id="proposal_1",
        user=user,
    )
    assert result["group_id"] == str(group_id)
    assert result["session_id"] == str(session_id)
    assert result["workflow_id"] == str(workflow_id)
    assert result["hr_review_session_id"] == str(hr_session_id)
    assert result["wake_up_message"].startswith("@")
    assert hr_session.status == "completed"
    assert hr_session.closed_at is not None


def _load_provision_project_agents():
    source = Path("app/services/project_provisioning.py").read_text()
    start = source.index("async def provision_project_agents(")
    end = source.index("async def ensure_team_directory_contacts(", start)

    class _ProjectProvisioningError(RuntimeError):
        pass

    namespace = {
        "AsyncSession": object,
        "datetime": datetime,
        "UTC": UTC,
        "load_active_model": AsyncMock(),
        "ProjectProvisioningError": _ProjectProvisioningError,
        "ensure_access_granted_platform_relationships": AsyncMock(),
        "agent_manager": SimpleNamespace(initialize_agent_files=AsyncMock()),
        "store_agent_bytes": AsyncMock(),
    }
    exec(source[start:end], namespace)
    return namespace


@pytest.mark.asyncio
async def test_provision_project_agents_writes_role_soul_bytes():
    provisioning = _load_provision_project_agents()
    provision_project_agents = provisioning["provision_project_agents"]
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    role = _role()
    agent = SimpleNamespace(
        id=agent_id,
        name=role["name"],
        status="creating",
        agent_type="native",
        primary_model_id=uuid.uuid4(),
    )
    participant = SimpleNamespace(display_name=role["name"])
    stored: list[tuple[uuid.UUID, str, bytes]] = []

    async def _store_agent_bytes(agent_id_arg, path, data):
        stored.append((agent_id_arg, path, data))

    class _Session:
        async def flush(self):
            return None

    provisioning["load_active_model"] = AsyncMock(return_value=SimpleNamespace(id=agent.primary_model_id))
    provisioning["ensure_access_granted_platform_relationships"] = AsyncMock()
    provisioning["agent_manager"].initialize_agent_files = AsyncMock()
    provisioning["store_agent_bytes"] = _store_agent_bytes

    await provision_project_agents(
        _Session(),
        agents=[(role, agent, participant)],
        creator_id=creator_id,
        tenant_id=tenant_id,
        default_model_id=uuid.uuid4(),
    )

    assert agent.status == "idle"
    assert len(stored) == 1
    stored_agent_id, stored_path, stored_bytes = stored[0]
    assert stored_agent_id == agent_id
    assert stored_path == "soul.md"
    assert stored_bytes == role["soul"].strip().encode("utf-8")


def test_provision_path_writes_role_soul_and_sets_group_owner():
    provisioning_source = Path("app/services/project_provisioning.py").read_text()
    assert "async def materialize_role_agent" not in provisioning_source
    assert "materialize_role_agent" in provisioning_source
    assert "group.owner_agent_id = leader_agent.id" in provisioning_source
    assert "store_agent_bytes(agent.id, \"soul.md\"" in provisioning_source

    team_builder_source = Path("app/services/project_team_builder.py").read_text()
    assert "async def materialize_role_agent" in team_builder_source

    hr_source = Path("app/services/hr_review_session_service.py").read_text()
    assert "provision_team_from_plan" in hr_source
    assert 'hr_session.status = "completed"' in hr_source

    api_source = Path("app/api/hr_review.py").read_text()
    assert "workflow_id: uuid.UUID" in api_source
    assert "group_id: uuid.UUID" in api_source
