"""P1.3 tests — workflow_composer + run_repository + provisioning hook integration.

Four coverage areas required by the task spec:

1. ``compose_initial_workflow`` writes a valid YAML file under tmp with all
   required AO fields populated.
2. ``create_run_row`` inserts three ``WorkflowRunStep`` rows in the right
   dependency order against a real SQLite schema built via
   ``Base.metadata.create_all``.
3. ``provision_team_from_plan`` end-to-end path (no real AO call): after a
   successful composition the workflow row has ``yaml_content`` non-empty and
   the four-power ``scheduler_agent_id`` etc. filled.
4. AO failure path: when ``compose_initial_workflow`` raises, the workflow
   still reaches ``active`` with ``yaml_content=None`` so P1.4 can retry.

The tests do NOT call the AO CLI; AO behaviour is mocked or stubbed.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.database import Base

# Import the modules under test once the conftest has adjusted sys.path.
from app.services.ao import (
    compose_initial_workflow,
    create_run_row,
    get_run_steps,
    mark_run_started,
)
from app.services.ao import workflow_composer as composer_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Override ``AO_*`` directories to point at tmp so tests don't pollute the repo."""
    monkeypatch.setenv("AO_WORKFLOWS_DIR", str(tmp_path / "workflows"))
    monkeypatch.setenv("AO_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("AO_OUTPUT_DIR", str(tmp_path / "home" / "output"))
    monkeypatch.setenv("AO_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("AO_PROVIDER", "openai")
    monkeypatch.setenv("AO_MODEL", "clawith-gateway")
    monkeypatch.setenv("AO_CONCURRENCY", "2")
    # The settings object is memoized; reset before re-reading.
    get_settings.cache_clear()
    return get_settings()


def _load_workflow_composer():
    """Import ``workflow_composer`` lazily so settings overrides apply."""
    if "app.services.ao.workflow_composer" in sys.modules:
        return importlib.reload(sys.modules["app.services.ao.workflow_composer"])
    return composer_module


def _make_workflow_stub(*, workflow_id: uuid.UUID | None = None) -> SimpleNamespace:
    """ProjectWorkflow stand-in with only the attributes the composer reads."""
    return SimpleNamespace(
        id=workflow_id or uuid.uuid4(),
        name="AI Launch Plan",
    )


class _NullSession:
    """Session stub accepted by ``compose_initial_workflow`` (which doesn't write)."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - stub
        return None

    async def flush(self) -> None:
        return None

    async def add(self, obj: Any) -> None:
        return None

    async def get(self, model: Any, key: Any) -> Any:
        return None


# ---------------------------------------------------------------------------
# 1. compose_initial_workflow — YAML shape
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reload_composer_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Apply tmp settings + reload ``workflow_composer`` so its cached settings are fresh.

    The composer memoises ``settings = get_settings()`` at import time; without
    this fixture the test would write YAML under ``~/.clawith/data/agents/ao``
    instead of the tmp path and pollute the developer's machine.
    """
    _seed_settings(tmp_path, monkeypatch)
    # The composer module memoises ``settings = get_settings()`` at import time;
    # reload it after the env-var seed so it picks up the tmp paths.
    importlib.reload(sys.modules["app.services.ao.workflow_composer"])
    yield


async def test_compose_initial_workflow_writes_yaml_with_all_required_fields(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    assert settings.AO_WORKFLOWS_DIR  # settings resolved under tmp

    workflow = _make_workflow_stub()
    scheduler_id = uuid.uuid4()
    quality_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    executor_id = uuid.uuid4()
    agent_ids = {
        "scheduler": scheduler_id,
        "quality": quality_id,
        "delivery": delivery_id,
        "executor_0": executor_id,
    }
    roles = [
        {
            "key": "executor_pm",
            "name": "PM",
            "duties": "Plan",
            "is_group_leader": True,
        },
        {
            "key": "executor_dev",
            "name": "Dev",
            "duties": "Build",
            "is_group_leader": False,
        },
    ]

    yaml_path, metadata = await compose_initial_workflow(
        _NullSession(),
        workflow=workflow,
        agent_ids=agent_ids,
        roles=roles,
    )

    assert yaml_path.is_file(), f"YAML not written at {yaml_path}"
    assert yaml_path.parent.resolve() == Path(settings.AO_WORKFLOWS_DIR).resolve()
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert parsed["name"] == workflow.name
    assert parsed["agents_dir"] == settings.AO_AGENTS_DIR
    assert parsed["llm"] == {
        "provider": settings.AO_PROVIDER,
        "model": settings.AO_MODEL,
    }
    assert parsed["concurrency"] == settings.AO_CONCURRENCY
    step_keys = [step["id"] for step in parsed["steps"]]
    assert step_keys == ["clarify", "execute", "review"]
    assert all(step["role"] for step in parsed["steps"])
    assert all("task" in step and step["task"] for step in parsed["steps"])
    clarify = parsed["steps"][0]
    execute = parsed["steps"][1]
    review = parsed["steps"][2]
    assert clarify["output"] == "plan"
    assert execute["depends_on"] == ["clarify"]
    assert execute["output"] == "artifact"
    assert review["depends_on"] == ["execute"]
    assert metadata["step_count"] == 3
    assert metadata["yaml_text"] == yaml_path.read_text(encoding="utf-8")


async def test_compose_initial_workflow_falls_back_when_no_executor_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roles list contains only power-slot keys → executor uses the fallback path."""
    # autouse fixture already seeded + reloaded composer.
    composer = sys.modules["app.services.ao.workflow_composer"]
    workflow = _make_workflow_stub()
    agent_ids = {
        "scheduler": uuid.uuid4(),
        "quality": uuid.uuid4(),
        "delivery": uuid.uuid4(),
    }
    roles = [
        {"key": "scheduler", "name": "Sch", "duties": "Sched", "is_group_leader": True},
        {"key": "quality", "name": "Q", "duties": "Q", "is_group_leader": False},
        {"key": "delivery", "name": "D", "duties": "D", "is_group_leader": False},
    ]
    yaml_path, metadata = await composer.compose_initial_workflow(
        _NullSession(),
        workflow=workflow,
        agent_ids=agent_ids,
        roles=roles,
    )
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    # 兜底 role_path 应为 ``product/product-manager``，以保证 executor 步骤仍可执行。
    assert parsed["steps"][1]["role"] == composer._FALLBACK_ROLE_PATH
    assert metadata["executor_role_path"] == composer._FALLBACK_ROLE_PATH


# ---------------------------------------------------------------------------
# 2. create_run_row — three DAG rows against a real SQLite schema
# ---------------------------------------------------------------------------


@pytest.fixture
async def sqlite_session():
    """Yield an async session backed by a fresh sqlite :memory: database.

    ``Base.metadata.create_all`` is used (per the task spec) so we never need
    Alembic. The fixture is function-scoped so tests stay isolated. JSONB
    columns are swapped for portable ``JSON`` and any ``server_default``
    literal containing ``::jsonb`` casts is stripped before the DDL runs —
    the SQLite dialect cannot compile either.
    """
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB

    from app.models.project import ProjectWorkflow
    from app.models.workflow_run import WorkflowRunStep

    jsonb_swaps: dict[Any, Any] = {}
    server_default_swaps: dict[Any, Any] = {}
    for table in (ProjectWorkflow.__table__, WorkflowRunStep.__table__):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_swaps[column] = column.type
                column.type = JSON()
            if column.server_default is not None and "::jsonb" in str(column.server_default.arg):
                server_default_swaps[column] = column.server_default
                column.server_default = None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[ProjectWorkflow.__table__, WorkflowRunStep.__table__],
            )
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as session:
            yield session
    finally:
        for column, original in jsonb_swaps.items():
            column.type = original
        for column, original in server_default_swaps.items():
            column.server_default = original
        await engine.dispose()


async def _ensure_workflow_row(sqlite_session: AsyncSession, *, tenant_id: uuid.UUID) -> SimpleNamespace:
    """Insert a minimal ``project_workflows`` row so the FK target exists.

    The test only cares that ``workflow_run_steps.workflow_id`` resolves, so
    we lean on the ``ProjectWorkflow`` model directly — keeping the fixture
    simple.
    """
    from app.models.project import ProjectWorkflow

    workflow = ProjectWorkflow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="AI Launch Plan",
        template_key="hr_generated",
        requirements="Build the AI launch pipeline.",
        status="active",
        team_plan={"roles": []},
    )
    sqlite_session.add(workflow)
    await sqlite_session.flush()
    return workflow


async def test_create_run_row_inserts_three_steps_in_dependency_order(
    sqlite_session: AsyncSession,
) -> None:
    tenant_id = uuid.uuid4()
    workflow = await _ensure_workflow_row(sqlite_session, tenant_id=tenant_id)
    scheduler_id = uuid.uuid4()

    rows = await create_run_row(
        sqlite_session,
        workflow=workflow,
        yaml_text="name: x\nagents_dir: ./agents\nllm: {provider: openai, model: m}\nsteps: []\n",
        run_dir=Path("/tmp/run"),
        agent_ids={"scheduler": scheduler_id, "executor_0": uuid.uuid4(), "quality": uuid.uuid4()},
    )

    assert len(rows) == 3
    step_keys = [row.step_key for row in rows]
    assert step_keys == ["clarify", "execute", "review"]
    # 依赖关系：clarify 无依赖，execute depends_on clarify，review depends_on execute。
    assert rows[0].depends_on == []
    assert rows[1].depends_on == ["clarify"]
    assert rows[2].depends_on == ["execute"]
    # 默认状态：pending。
    assert all(row.status == "pending" for row in rows)
    # 必备位 id 已绑定到对应 step。
    assert rows[0].agent_id == scheduler_id


async def test_get_run_steps_returns_steps_in_order(sqlite_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    workflow = await _ensure_workflow_row(sqlite_session, tenant_id=tenant_id)
    await create_run_row(
        sqlite_session,
        workflow=workflow,
        yaml_text="name: x\nagents_dir: ./agents\nllm: {provider: openai, model: m}\nsteps: []\n",
        run_dir=Path("/tmp/run"),
    )

    fetched = await get_run_steps(sqlite_session, workflow_id=workflow.id)
    assert [row.step_key for row in fetched] == ["clarify", "execute", "review"]


async def test_mark_run_started_sets_status_and_started_at(sqlite_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    workflow = await _ensure_workflow_row(sqlite_session, tenant_id=tenant_id)

    await mark_run_started(sqlite_session, workflow_id=workflow.id)

    refreshed = await sqlite_session.get(type(workflow), workflow.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.started_at is not None


# ---------------------------------------------------------------------------
# 3 & 4. provision_team_from_plan — integration with mocked AO + degradation
# ---------------------------------------------------------------------------


def _fake_role_dicts() -> list[dict]:
    return [
        {
            "key": "pm",
            "name": "PM",
            "duties": "Plan",
            "soul": "# PM\nYou lead.",
            "is_group_leader": True,
            "suggested_tools": [],
            "suggested_permissions": {"scope_type": "company", "access_level": "use"},
        },
        {
            "key": "dev",
            "name": "Dev",
            "duties": "Build",
            "soul": "# Dev\nYou build.",
            "is_group_leader": False,
            "suggested_tools": [],
            "suggested_permissions": {"scope_type": "company", "access_level": "use"},
        },
    ]


class _StubProvisioningSession:
    """Lightweight AsyncSession stand-in that captures additions and supports the few
    service queries ``provision_team_from_plan`` needs.

    It supports a fluent interface used by the workflow_role_seeder stub:
    each ``add()`` records the object in ``added``; ``flush()`` increments a
    counter; ``scalar`` returns a stub GroupMember so the "owner membership
    created" assertion in ``provision_team_from_plan`` does not abort.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_count = 0
        self._scalar_call_count = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def execute(self, statement: Any) -> Any:
        return None

    async def scalar(self, statement: Any) -> Any:
        # The provisioning flow calls ``await db.scalar(select(GroupMember)...)``
        # once per group-leader check. Return a fake owner so the assertion
        # that membership exists passes.
        self._scalar_call_count += 1
        return SimpleNamespace(role="member", removed_at=None)

    async def get(self, model: Any, key: Any) -> Any:
        return None


def _install_provisioning_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    compose_impl,
    ensure_roles_impl,
    capture: dict[str, Any] | None = None,
) -> None:
    """Wire up every collaborator ``provision_team_from_plan`` reaches.

    ``compose_impl`` is the function that will be substituted for
    ``compose_initial_workflow``; ``ensure_roles_impl`` for
    ``ensure_workflow_system_roles``. ``capture`` is an optional dict the
    tests can inspect to verify side effects (e.g. agent_ids passed to
    compose).
    """
    from app.services import project_provisioning

    async def fake_create_run_row(db, *, workflow, yaml_text, run_dir, agent_ids=None):
        return []

    async def fake_materialize_role(db, *, tenant_id, creator_id, project_name, role, default_model_id, tenant):
        return (
            role,
            SimpleNamespace(
                id=uuid.uuid4(),
                name=role["name"],
                agent_type="native",
                status="creating",
                primary_model_id=default_model_id,
                deleted_at=None,
            ),
            SimpleNamespace(id=uuid.uuid4()),
        )

    async def fake_provision_project_agents(db, *, agents, creator_id, tenant_id, default_model_id):
        pass

    async def fake_directory(db, *, agents, created_by_user_id):
        pass

    async def fake_decision(db, *, workflow, human_participant, agents):
        workflow.decision_group_id = uuid.uuid4()

    async def fake_sync_shareholder(db, *, tenant_id, leader_agent):
        pass

    monkeypatch.setattr(project_provisioning, "ensure_workflow_system_roles", ensure_roles_impl)
    monkeypatch.setattr(project_provisioning, "compose_initial_workflow", compose_impl)
    monkeypatch.setattr(project_provisioning, "create_run_row", fake_create_run_row)
    monkeypatch.setattr(project_provisioning, "materialize_role_agent", fake_materialize_role)
    monkeypatch.setattr(project_provisioning, "provision_project_agents", fake_provision_project_agents)
    monkeypatch.setattr(project_provisioning, "ensure_team_directory_contacts", fake_directory)
    monkeypatch.setattr(project_provisioning, "ensure_project_decision_group", fake_decision)
    monkeypatch.setattr(project_provisioning, "sync_shareholder_group_with_project_leader", fake_sync_shareholder)

    fake_group = SimpleNamespace(id=uuid.uuid4(), owner_agent_id=None)

    async def fake_create_group(db, *, tenant_id, creator_participant_id, name, description, member_participant_ids):
        return fake_group

    async def fake_create_group_session(db, *, tenant_id, group_id, actor_participant_id, title):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(project_provisioning.group_chat_service, "create_group", fake_create_group)
    monkeypatch.setattr(project_provisioning.group_chat_service, "create_group_session", fake_create_group_session)

    async def fake_enqueue(db, *, tenant_id, group_id, session_id, sender_participant_id, content, mention_participant_ids, message_id, **kwargs):
        return SimpleNamespace(message=SimpleNamespace(id=uuid.uuid4()), dispatch_kind="group_chat")

    # ``group_message_service`` is imported inline inside the function body,
    # so we patch BOTH ``sys.modules`` (for ``import ... `` resolutions) and the
    # ``app.services`` package attribute (for ``from app.services import ...``).
    class _GMSError(Exception):
        pass

    gms_stub = SimpleNamespace(
        enqueue_group_message=fake_enqueue,
        GroupMessageServiceError=_GMSError,
    )
    import app.services as _app_services_pkg
    monkeypatch.setattr(_app_services_pkg, "group_message_service", gms_stub, raising=False)
    monkeypatch.setitem(sys.modules, "app.services.group_message_service", gms_stub)

    async def fake_default_model(db, *, tenant, tenant_id):
        return uuid.uuid4()

    monkeypatch.setattr(project_provisioning, "project_default_model_id", fake_default_model)

    async def fake_get_or_create_user_participant(db, user_id, display_name, avatar_url):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(project_provisioning, "get_or_create_user_participant", fake_get_or_create_user_participant)

    # Capture dict lets the test inspect what flowed into compose.
    if capture is not None:
        capture["_installed"] = True


async def test_provision_team_from_plan_writes_yaml_and_agents(monkeypatch) -> None:
    """Successful compose path: workflow ends with yaml_content and slot ids filled."""
    from app.services import project_provisioning

    captured: dict[str, Any] = {}

    async def fake_compose(db, *, workflow, agent_ids, roles):
        captured["agent_ids"] = agent_ids
        captured["roles"] = roles
        captured["workflow_id"] = workflow.id
        yaml_path = Path("/tmp/ao-stub-workflow.yaml")
        yaml_path.write_text(
            "name: stub\nagents_dir: ./agents\nllm: {provider: openai, model: x}\n"
            "concurrency: 2\nsteps: []\n",
            encoding="utf-8",
        )
        return yaml_path, {
            "yaml_text": yaml_path.read_text(encoding="utf-8"),
            "step_count": 3,
            "executor_role_path": "product/executor-pm",
            "executor_agent_id": None,
            "step_role_paths": {
                "clarify": "product/project-scheduler",
                "execute": "product/executor-pm",
                "review": "quality/quality-reviewer",
            },
        }

    async def fake_ensure_roles(db, *, tenant_id, creator_id, model_id):
        return {
            "scheduler": SimpleNamespace(id=uuid.uuid4(), role_description="workflow.scheduler"),
            "quality": SimpleNamespace(id=uuid.uuid4(), role_description="workflow.quality"),
            "delivery": SimpleNamespace(id=uuid.uuid4(), role_description="workflow.delivery"),
        }

    _install_provisioning_stubs(
        monkeypatch,
        compose_impl=fake_compose,
        ensure_roles_impl=fake_ensure_roles,
        capture=captured,
    )

    session = _StubProvisioningSession()
    async def db_get(model: Any, key: Any) -> Any:
        return SimpleNamespace(
            default_model_id=uuid.uuid4(),
            default_max_llm_calls_per_day=1000,
            default_max_triggers=20,
            min_poll_interval_floor=5,
            max_webhook_rate_ceiling=5,
            min_heartbeat_interval_minutes=240,
        )

    session.get = db_get  # type: ignore[method-assign]

    result = await project_provisioning.provision_team_from_plan(
        session,
        tenant_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        creator_display_name="Tester",
        creator_avatar_url=None,
        project_name="AI Launch Plan",
        requirements="Build the AI launch pipeline.",
        roles=_fake_role_dicts(),
    )

    workflow = result["workflow"]
    assert workflow.yaml_content is not None
    assert workflow.yaml_content.startswith("name:")
    assert workflow.scheduler_agent_id is not None
    assert workflow.quality_agent_id is not None
    assert workflow.delivery_agent_id is not None
    # executor_agent_ids 包含 pm 以外的 role key
    assert "dev" in workflow.executor_agent_ids
    # 失败回退：未发生异常，状态为 active
    assert workflow.status == "active"
    # 确认 capture 中的 agent_ids 含必备位
    assert set(captured["agent_ids"]) == {"scheduler", "quality", "delivery"}


async def test_provision_team_from_plan_degrades_when_compose_fails(monkeypatch) -> None:
    """AO compose raises → workflow is still active with yaml_content=None."""
    from app.services import project_provisioning

    boom_calls: list[str] = []

    async def boom_compose(db, *, workflow, agent_ids, roles):
        boom_calls.append("compose")
        raise RuntimeError("intentional AO failure")

    async def fake_ensure_roles(db, *, tenant_id, creator_id, model_id):
        return {
            "scheduler": SimpleNamespace(id=uuid.uuid4(), role_description="workflow.scheduler"),
            "quality": SimpleNamespace(id=uuid.uuid4(), role_description="workflow.quality"),
            "delivery": SimpleNamespace(id=uuid.uuid4(), role_description="workflow.delivery"),
        }

    _install_provisioning_stubs(
        monkeypatch,
        compose_impl=boom_compose,
        ensure_roles_impl=fake_ensure_roles,
    )

    session = _StubProvisioningSession()
    async def db_get(model: Any, key: Any) -> Any:
        return SimpleNamespace(
            default_model_id=uuid.uuid4(),
            default_max_llm_calls_per_day=1000,
            default_max_triggers=20,
            min_poll_interval_floor=5,
            max_webhook_rate_ceiling=5,
            min_heartbeat_interval_minutes=240,
        )

    session.get = db_get  # type: ignore[method-assign]

    result = await project_provisioning.provision_team_from_plan(
        session,
        tenant_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        creator_display_name="Tester",
        creator_avatar_url=None,
        project_name="AI Launch Plan",
        requirements="Build the AI launch pipeline.",
        roles=_fake_role_dicts(),
    )

    workflow = result["workflow"]
    assert boom_calls == ["compose"], "compose must have been attempted exactly once"
    assert workflow.yaml_content is None, "yaml_content must be None when compose fails"
    assert workflow.status == "active", "workflow still reaches active even if AO compose failed"


async def test_compose_initial_workflow_helper_resolves_scheduler_role_path() -> None:
    """``_ao_role_path_for`` returns the canonical hint for the three power slots."""
    agent = SimpleNamespace(id=uuid.uuid4(), role_description="")
    assert composer_module._ao_role_path_for(agent, "scheduler") == "product/project-scheduler"
    assert composer_module._ao_role_path_for(agent, "quality") == "quality/quality-reviewer"
    assert composer_module._ao_role_path_for(agent, "delivery") == "delivery/delivery-coordinator"


async def test_compose_initial_workflow_helper_respects_role_description() -> None:
    agent = SimpleNamespace(id=uuid.uuid4(), role_description="custom/custom-role")
    # role_key 不在 hint 表 → 走 role_description 路径
    assert composer_module._ao_role_path_for(agent, "executor_0") == "custom/custom-role"