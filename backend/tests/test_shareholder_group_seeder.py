"""Shareholder group seeder and governance role defaults."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.agent import Agent
from app.models.group import Group, GroupMember
from app.models.project import ShareholderGroup
from app.models.user import User


def _load_ensure_shareholder_group(monkeypatch):
    mock_gov = MagicMock()
    mock_gov.seed_governance_role_pool_for_tenant = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.governance_seeder", mock_gov)

    mock_gcs = MagicMock()
    mock_gcs.create_group = AsyncMock()
    mock_gcs.create_group_session = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.group_chat_service", mock_gcs)

    mock_pi = MagicMock()
    mock_pi.get_or_create_agent_participant = AsyncMock()
    mock_pi.get_or_create_user_participant = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.participant_identity", mock_pi)

    path = Path("app/services/shareholder_group_seeder.py")
    spec = importlib.util.spec_from_file_location("shareholder_group_seeder_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cfo_default_enabled():
    source = Path("app/services/governance_seeder.py").read_text()
    assert '("cfo", "decision", "CFO Agent", True)' in source


def test_board_secretary_role_defined():
    source = Path("app/services/governance_seeder.py").read_text()
    assert "SECRETARY_ROLES" in source
    assert '("board_secretary", "decision", "Board Secretary", True)' in source


def test_seed_includes_secretary_roles():
    source = Path("app/services/governance_seeder.py").read_text()
    assert "*SECRETARY_ROLES" in source


def test_shareholder_group_seeder_sets_board_secretary_owner():
    source = Path("app/services/shareholder_group_seeder.py").read_text()
    assert "board_secretary" in source
    assert "owner_agent_id = board_secretary.id" in source
    assert 'SHAREHOLDER_GROUP_TYPE = "shareholder"' in source
    assert '"board_secretary"' in source


def test_create_shareholder_group_calls_ensure():
    api_source = Path("app/api/projects.py").read_text()
    create_start = api_source.index("async def create_shareholder_group(")
    create_end = api_source.index("async def get_shareholder_board(", create_start)
    create_route = api_source[create_start:create_end]
    assert "ensure_shareholder_group" in create_route


def test_tenant_create_hooks_shareholder_ensure():
    tenants_source = Path("app/api/tenants.py").read_text()
    assert "try_ensure_governance_groups_for_tenant" in tenants_source
    assert "tenant.self_create" in tenants_source or "tenant.join" in tenants_source


def test_admin_backfill_hooks_shareholder_ensure():
    admin_source = Path("app/api/admin.py").read_text()
    assert "try_ensure_governance_groups_for_tenant" in admin_source
    assert "backfill_governance_groups" in admin_source


def test_auth_register_init_hooks_governance_ensure():
    auth_source = Path("app/api/auth.py").read_text()
    assert "try_ensure_governance_groups_for_tenant" in auth_source
    assert "auth.register_init" in auth_source


def test_startup_backfills_governance_groups():
    main_source = Path("app/main.py").read_text()
    assert "backfill_governance_groups_for_all_tenants" in main_source


def test_backfill_script_exists():
    script = Path("app/scripts/backfill_governance_groups.py")
    assert script.is_file()
    source = script.read_text()
    assert "backfill_governance_groups_for_all_tenants" in source


@pytest.mark.asyncio
async def test_ensure_group_members_idempotent(monkeypatch):
    """Calling _ensure_group_members twice must not duplicate active members."""
    module = _load_ensure_shareholder_group(monkeypatch)

    group_id = uuid.uuid4()
    participant_ids = [uuid.uuid4(), uuid.uuid4()]
    stored_members: list[GroupMember] = []

    class _FakeDB:
        async def execute(self, _statement):
            class _Result:
                def scalars(self):
                    class _Scalars:
                        def all(self):
                            return [
                                member.participant_id
                                for member in stored_members
                                if member.removed_at is None
                            ]

                    return _Scalars()

            return _Result()

        async def scalar(self, _statement):
            return None

        async def flush(self) -> None:
            return None

        def add(self, obj) -> None:
            if isinstance(obj, GroupMember):
                stored_members.append(obj)

    db = _FakeDB()
    await module._ensure_group_members(
        db,
        group_id=group_id,
        participant_ids=participant_ids,
    )
    assert len(stored_members) == 2

    await module._ensure_group_members(
        db,
        group_id=group_id,
        participant_ids=participant_ids,
    )
    assert len(stored_members) == 2


@pytest.mark.asyncio
async def test_ensure_shareholder_group_sets_board_secretary_owner_even_with_project_leader(
    monkeypatch,
):
    """Project leaders are no longer 群主; ensure always assigns Board Secretary."""
    module = _load_ensure_shareholder_group(monkeypatch)

    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    board_secretary_id = uuid.uuid4()
    project_leader_id = uuid.uuid4()
    group_id = uuid.uuid4()

    governance_agents = {}
    for role_key in module.SHAREHOLDER_MEMBER_ROLE_KEYS:
        agent_id = board_secretary_id if role_key == "board_secretary" else uuid.uuid4()
        governance_agents[role_key] = Agent(
            id=agent_id,
            tenant_id=tenant_id,
            creator_id=creator_id,
            name=role_key,
            status="idle",
            is_expired=False,
        )

    # Legacy state: shareholder group owner was a project leader (old 群主 candidate).
    group = SimpleNamespace(
        id=group_id,
        tenant_id=tenant_id,
        name="股东群",
        group_type=None,
        owner_agent_id=project_leader_id,
        deleted_at=None,
    )
    shareholder_row = ShareholderGroup(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=group_id,
        creator_id=creator_id,
    )
    creator = User(
        id=creator_id,
        tenant_id=tenant_id,
        display_name="Admin",
        role="admin",
        is_active=True,
    )
    creator_participant = SimpleNamespace(id=uuid.uuid4())
    agent_participants = {
        agent.id: SimpleNamespace(id=uuid.uuid4()) for agent in governance_agents.values()
    }

    class _ExistingGroupDB:
        def __init__(self) -> None:
            self.flush_count = 0

        async def scalar(self, _statement):
            return shareholder_row

        async def get(self, model, obj_id):
            if model is User:
                return creator
            if model is Group and obj_id == group_id:
                return group
            return None

        async def flush(self) -> None:
            self.flush_count += 1

    db = _ExistingGroupDB()

    async def fake_pool_agent(_db, *, tenant_id, role_key):
        return governance_agents[role_key]

    monkeypatch.setattr(module, "_pool_agent", fake_pool_agent)
    monkeypatch.setattr(
        module,
        "get_or_create_user_participant",
        AsyncMock(return_value=creator_participant),
    )
    monkeypatch.setattr(
        module,
        "get_or_create_agent_participant",
        AsyncMock(side_effect=lambda _db, agent_id, **kwargs: agent_participants[agent_id]),
    )
    monkeypatch.setattr(module, "_ensure_group_members", AsyncMock())
    monkeypatch.setattr(
        module.group_chat_service,
        "create_group_session",
        AsyncMock(),
    )

    result = await module.ensure_shareholder_group(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=None,
    )

    assert result.owner_agent_id == board_secretary_id
    assert result.owner_agent_id != project_leader_id
    assert group.group_type == module.SHAREHOLDER_GROUP_TYPE
