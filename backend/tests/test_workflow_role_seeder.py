"""Idempotency / multi-tenant tests for workflow role seeder (P1.2)."""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest

from app.models.agent import Agent, AgentTemplate
from app.services import workflow_role_seeder

# ---------------------------------------------------------------------------
# Helpers — minimal in-memory session that runs the seeder's SQL by hand
# ---------------------------------------------------------------------------


class _RowBag:
    """Trivial in-memory store for ORM rows used by the seeder."""

    def __init__(self):
        self.templates: dict[tuple[str, bool], AgentTemplate] = {}
        # keyed by (tenant_id, name)
        self.agents: dict[tuple[uuid.UUID, str], Agent] = {}
        self.permissions: list[tuple[uuid.UUID, str]] = []
        self.participants: list[tuple[uuid.UUID, str]] = []
        self.flush_count = 0


class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=list)


_TABLE_FROM_STATEMENT_RE = re.compile(r'FROM\s+"?([a-zA-Z_][\w]*)"?')


class _FakeSession:
    """Async-compatible session that supports only what the seeder needs.

    Inspects each ``SELECT`` statement by its SQL string, identifies the
    target table and WHERE predicates, and answers from the in-memory
    ``_RowBag``.  All other session methods are no-ops (the seeder only
    relies on ``execute``, ``scalar``, ``flush``, ``add``, ``add_all``).
    """

    def __init__(self, bag: _RowBag):
        self._bag = bag

    async def execute(self, statement):

        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        table_match = _TABLE_FROM_STATEMENT_RE.search(sql)
        if not table_match:
            return _Result()
        table = table_match.group(1)
        if table == "agent_templates":
            return _Result(scalar=_match_template_select(self._bag, sql))
        if table == "agents":
            return _Result(scalar=_match_agent_select(self._bag, sql))
        return _Result()

    async def scalar(self, statement):
        return (await self.execute(statement))._scalar

    async def get(self, model, obj_id):
        return None

    async def flush(self):
        self._bag.flush_count += 1

    async def commit(self):
        return None

    async def rollback(self):
        return None

    def add(self, obj):
        if isinstance(obj, AgentTemplate):
            self._bag.templates[(obj.name, bool(obj.is_builtin))] = obj
        elif isinstance(obj, Agent):
            self._bag.agents[(obj.tenant_id, obj.name)] = obj
        elif isinstance(obj, SimpleNamespace):
            # Participant row — store by (ref_id, display_name).
            self._bag.participants.append((obj.ref_id, getattr(obj, "display_name", "")))
        else:
            name = getattr(obj, "__class__", SimpleNamespace()).__name__
            if name == "AgentPermission" or hasattr(obj, "access_level"):
                self._bag.permissions.append((obj.agent_id, getattr(obj, "access_level", "use")))

    def add_all(self, objs):
        for obj in objs:
            self.add(obj)


def _normalize_predicate(predicate: str) -> str:
    """Collapse ``agents.name`` / ``"agents"."name"`` to ``name`` for matching."""
    predicate = predicate.replace('"', "")
    predicate = re.sub(r"[a-zA-Z_]\w*\.", "", predicate)
    return predicate.strip()


def _where_predicates(sql: str) -> list[str]:
    if "WHERE" not in sql:
        return []
    where = sql.split("WHERE", 1)[1]
    for stop in (" ORDER BY ", " LIMIT ", " GROUP BY "):
        idx = where.find(stop)
        if idx != -1:
            where = where[:idx]
    return [chunk.strip() for chunk in where.split("AND")]


def _find_predicate_value(sql: str, column: str) -> str | None:
    """Return the RHS of an equality / IS-true predicate on the given column."""
    pattern = rf"{column}\s*=\s*'?([^'\s]+)'?"
    for predicate in _where_predicates(sql):
        normalized = _normalize_predicate(predicate)
        match = re.match(pattern, normalized)
        if match:
            return match.group(1)
    return None


def _has_is_true(sql: str, column: str) -> bool:
    pattern = rf"{column}\s+IS\s+true"
    return any(re.search(pattern, _normalize_predicate(p)) for p in _where_predicates(sql))


def _has_is_null(sql: str, column: str) -> bool:
    pattern = rf"{column}\s+IS\s+NULL"
    return any(re.search(pattern, _normalize_predicate(p)) for p in _where_predicates(sql))


def _match_template_select(bag: _RowBag, sql: str) -> AgentTemplate | None:
    name_value = _find_predicate_value(sql, "name")
    if name_value is None:
        return None
    if not _has_is_true(sql, "is_builtin"):
        return None
    return bag.templates.get((name_value, True))


def _match_agent_select(bag: _RowBag, sql: str) -> Agent | None:
    name_value = _find_predicate_value(sql, "name")
    tenant_value = _find_predicate_value(sql, "tenant_id")
    if name_value is None or tenant_value is None:
        return None
    if not _has_is_true(sql, "is_system"):
        return None
    try:
        tenant_id = uuid.UUID(tenant_value)
    except ValueError:
        return None
    require_alive = _has_is_null(sql, "deleted_at")
    agent = bag.agents.get((tenant_id, name_value))
    if agent is None:
        return None
    if require_alive and getattr(agent, "deleted_at", None) is not None:
        return None
    return agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bag() -> _RowBag:
    return _RowBag()


@pytest.fixture
def fake_session(bag):
    return _FakeSession(bag)


@pytest.fixture
def stub_external_io(monkeypatch, tmp_path):
    """Capture soul files written by the seeder via tmp_path."""

    captured: dict[str, bytes] = {}

    async def fake_store(agent_id, rel_path, data, **kwargs):
        key = f"{agent_id}/{rel_path}"
        captured[key] = bytes(data)
        return key

    monkeypatch.setattr(
        workflow_role_seeder,
        "store_agent_bytes",
        fake_store,
    )

    async def fake_init(_db, agent, **kwargs):
        return None

    monkeypatch.setattr(
        workflow_role_seeder.agent_manager,
        "initialize_agent_files",
        fake_init,
    )
    monkeypatch.setattr(
        workflow_role_seeder.settings,
        "AGENT_TEMPLATE_DIR",
        str(tmp_path),
    )

    return captured, tmp_path


# ---------------------------------------------------------------------------
# Pure-data structural checks
# ---------------------------------------------------------------------------


def test_system_roles_define_three_required_keys():
    assert set(workflow_role_seeder.SYSTEM_ROLES) == {"scheduler", "quality", "delivery"}


def test_each_system_role_soul_exceeds_200_chinese_characters():
    for role_key, spec in workflow_role_seeder.SYSTEM_ROLES.items():
        chinese_chars = sum(1 for c in spec.soul_body if "\u4e00" <= c <= "\u9fff")
        assert chinese_chars >= 200, (
            f"soul body for role_key={role_key!r} must contain at least 200 Chinese characters; got {chinese_chars}"
        )


def test_system_roles_carry_boundaries_in_soul_body():
    for spec in workflow_role_seeder.SYSTEM_ROLES.values():
        banned = ("不执行", "不定质量", "不调度", "不改原始业务稿", "不直接交付", "不改业务稿")
        assert any(token in spec.soul_body for token in banned), f"role {spec.role_key!r} lacks boundary text"


def test_each_spec_names_dispatch_tools_etc():
    expected_tools = {
        "scheduler": ("ao_parse_workflow", "dispatch_task_to_role"),
        "quality": ("quality_check_step", "submit_feedback_to_role"),
        "delivery": ("compile_delivery_package", "submit_approval_request"),
    }
    for role_key, tools in expected_tools.items():
        spec = workflow_role_seeder.SYSTEM_ROLES[role_key]
        for tool in tools:
            assert tool in spec.default_tools, f"role {role_key!r} missing tool {tool!r}"


# ---------------------------------------------------------------------------
# Seeding behavior (in-memory session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_ensure_creates_three_templates_three_agents_three_soul_files(
    stub_external_io,
    fake_session,
    bag,
):
    captured, tmp_path = stub_external_io
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()

    result = await workflow_role_seeder.ensure_workflow_system_roles(
        fake_session,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=None,
    )

    assert set(result) == {"scheduler", "quality", "delivery"}

    templates = list(bag.templates.values())
    agents = list(bag.agents.values())

    assert len(templates) == 3
    template_names = {t.name for t in templates}
    assert template_names == {"workflow_scheduler", "workflow_quality", "workflow_delivery"}
    for tmpl in templates:
        assert tmpl.is_builtin is True
        assert tmpl.soul_template
        assert tmpl.default_skills == []

    assert len(agents) == 3
    agent_names = {a.name for a in agents}
    assert agent_names == {"项目调度官", "质量评审官", "交付协调官"}
    for agent in agents:
        assert agent.is_system is True
        assert agent.reusable is False
        assert agent.tenant_id == tenant_id
        assert agent.heartbeat_enabled is False
        assert agent.status == "idle"
        assert agent.access_mode == "company"

    for role_key in ("scheduler", "quality", "delivery"):
        soul_path = tmp_path / f"workflow_{role_key}" / "soul.md"
        assert soul_path.is_file(), f"missing template soul file: {soul_path}"
        body = soul_path.read_text(encoding="utf-8")
        assert role_key in body
        assert "scope: system" in body

    for role_key, agent in result.items():
        key = f"{agent.id}/soul.md"
        assert key in captured, f"missing agent-side soul for {role_key}"


@pytest.mark.asyncio
async def test_second_ensure_is_idempotent_for_same_tenant(
    stub_external_io,
    fake_session,
    bag,
):
    _captured, tmp_path = stub_external_io
    tenant_id = uuid.uuid4()
    creator_id = uuid.uuid4()

    first = await workflow_role_seeder.ensure_workflow_system_roles(
        fake_session,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=None,
    )
    first_ids = {rk: agent.id for rk, agent in first.items()}

    second = await workflow_role_seeder.ensure_workflow_system_roles(
        fake_session,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=None,
    )
    second_ids = {rk: agent.id for rk, agent in second.items()}

    assert second_ids == first_ids

    templates = list(bag.templates.values())
    agents = list(bag.agents.values())
    assert len(templates) == 3
    assert len(agents) == 3

    for role_key in ("scheduler", "quality", "delivery"):
        soul_path = tmp_path / f"workflow_{role_key}" / "soul.md"
        assert soul_path.is_file(), f"missing template soul file: {soul_path}"


@pytest.mark.asyncio
async def test_multiple_tenants_get_independent_agents_but_share_templates(
    stub_external_io,
    fake_session,
    bag,
):
    _captured, tmp_path = stub_external_io
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    creator_a = uuid.uuid4()
    creator_b = uuid.uuid4()

    roles_a = await workflow_role_seeder.ensure_workflow_system_roles(
        fake_session,
        tenant_id=tenant_a,
        creator_id=creator_a,
        model_id=None,
    )
    roles_b = await workflow_role_seeder.ensure_workflow_system_roles(
        fake_session,
        tenant_id=tenant_b,
        creator_id=creator_b,
        model_id=None,
    )

    assert set(roles_a) == set(roles_b) == {"scheduler", "quality", "delivery"}

    for role_key in ("scheduler", "quality", "delivery"):
        assert roles_a[role_key].id != roles_b[role_key].id
        assert roles_a[role_key].tenant_id == tenant_a
        assert roles_b[role_key].tenant_id == tenant_b

    templates = list(bag.templates.values())
    agents = list(bag.agents.values())
    assert len(templates) == 3
    assert len(agents) == 6

    for role_key in ("scheduler", "quality", "delivery"):
        soul_path = tmp_path / f"workflow_{role_key}" / "soul.md"
        assert soul_path.is_file(), f"missing soul file for {role_key}"


@pytest.mark.asyncio
async def test_try_ensure_returns_none_on_failure(
    stub_external_io,
    fake_session,
    monkeypatch,
):
    async def boom(*args, **kwargs):
        raise RuntimeError("intentional failure for test")

    monkeypatch.setattr(workflow_role_seeder, "ensure_workflow_system_roles", boom)

    result = await workflow_role_seeder.try_ensure_workflow_system_roles(
        fake_session,
        tenant_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        model_id=None,
        context="unit-test",
    )

    assert result is None
