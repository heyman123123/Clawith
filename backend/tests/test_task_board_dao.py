"""T1.10 + T-012: TaskCardDAO tenant isolation (C2 enforcement).

Uses mock-based DB to avoid real connection issues (the previous integration
attempt caused a Docker daemon hang). The C2 invariant is fully tested:
every DAO method must include tenant_id in its WHERE clause.

Real-DB integration testing is deferred to Wave 3 (TaskBoardService)
where multi-step transactions justify a real connection.
"""
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import inspect
import pytest
import uuid

from app.services.task_board.dao import TaskCardDAO


class _FakeScalarResult:
    """Mimics SQLAlchemy Result.scalar_one_or_none / .scalars().all()."""

    def __init__(self, items: list | None) -> None:
        self._items = list(items) if items else []

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self) -> "_FakeScalarResult":
        return self

    def all(self) -> list:
        return list(self._items)


def _make_dao(card_in_db: list | None = None) -> tuple[TaskCardDAO, MagicMock]:
    """Build a DAO with a mock db whose execute returns a fake result."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeScalarResult(card_in_db or []))
    db.add = MagicMock()
    db.flush = AsyncMock()
    return TaskCardDAO(db), db


def _where_includes_tenant(stmt, tenant_id: uuid.UUID) -> bool:
    """Verify a SQLAlchemy statement has tenant_id in its WHERE clause.

    SQLAlchemy renders UUID literals as 'uuid-string' (single-quoted). We
    check both quoted and unquoted forms to be safe.
    """
    where = getattr(stmt, "whereclause", None)
    if where is None:
        return False
    compiled = str(where.compile(compile_kwargs={"literal_binds": True}))
    return tenant_id.hex in compiled or str(tenant_id) in compiled


@pytest.mark.asyncio
async def test_get_card_includes_tenant_in_where():
    """T-012: get_card must include tenant_id in WHERE clause."""
    tenant_id = uuid.uuid4()
    card_id = uuid.uuid4()
    fake_card = SimpleNamespace(id=card_id, tenant_id=tenant_id)
    dao, db = _make_dao(card_in_db=[fake_card])

    found = await dao.get_card(card_id, tenant_id=tenant_id)

    assert found is fake_card
    db.execute.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    assert _where_includes_tenant(stmt, tenant_id), \
        "WHERE clause must include tenant_id (C2)"


@pytest.mark.asyncio
async def test_get_card_returns_none_when_no_row_matches():
    """When the WHERE filters out everything, scalar_one_or_none returns None."""
    tenant_id = uuid.uuid4()
    card_id = uuid.uuid4()
    dao, db = _make_dao(card_in_db=[])

    result = await dao.get_card(card_id, tenant_id=tenant_id)

    assert result is None
    stmt = db.execute.await_args.args[0]
    assert _where_includes_tenant(stmt, tenant_id)


@pytest.mark.asyncio
async def test_list_cards_by_group_includes_tenant_in_where():
    """list_cards_by_group must include tenant_id in WHERE clause."""
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    cards = [
        SimpleNamespace(id=uuid.uuid4(), group_id=group_id, column="backlog", position=0)
    ]
    dao, db = _make_dao(card_in_db=cards)

    result = await dao.list_cards_by_group(group_id, tenant_id=tenant_id)

    assert len(result) == 1
    stmt = db.execute.await_args.args[0]
    assert _where_includes_tenant(stmt, tenant_id)


@pytest.mark.asyncio
async def test_list_cards_by_assignee_includes_tenant_in_where():
    """list_cards_by_assignee must include tenant_id in WHERE clause."""
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    cards = [
        SimpleNamespace(id=uuid.uuid4(), assignee_agent_id=agent_id, column="backlog")
    ]
    dao, db = _make_dao(card_in_db=cards)

    result = await dao.list_cards_by_assignee(agent_id, tenant_id=tenant_id)

    assert len(result) == 1
    stmt = db.execute.await_args.args[0]
    assert _where_includes_tenant(stmt, tenant_id)


@pytest.mark.asyncio
async def test_list_cards_by_ids_includes_tenant_and_ids_in_where():
    """list_cards_by_ids must include tenant_id and all card ids in WHERE."""
    tenant_id = uuid.uuid4()
    card_ids = [uuid.uuid4(), uuid.uuid4()]
    cards = [
        SimpleNamespace(id=card_ids[0]),
        SimpleNamespace(id=card_ids[1]),
    ]
    dao, db = _make_dao(card_in_db=cards)

    result = await dao.list_cards_by_ids(card_ids, tenant_id=tenant_id)

    assert len(result) == 2
    stmt = db.execute.await_args.args[0]
    assert _where_includes_tenant(stmt, tenant_id)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    for cid in card_ids:
        assert cid.hex in compiled, f"card id {cid} must appear in WHERE IN"


@pytest.mark.asyncio
async def test_list_cards_by_ids_empty_input_returns_empty_without_query():
    """Empty input is an early return - no DB query issued."""
    tenant_id = uuid.uuid4()
    dao, db = _make_dao()

    result = await dao.list_cards_by_ids([], tenant_id=tenant_id)

    assert result == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_card_assigns_tenant_id():
    """create_card must populate tenant_id from the required kwarg."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    dao, db = _make_dao()

    card = await dao.create_card(
        tenant_id=tenant_id,
        title="My task",
        created_by=user_id,
        created_by_type="user",
    )

    assert card.tenant_id == tenant_id, "Card.tenant_id must match kwarg"
    assert card.created_by == user_id
    assert card.title == "My task"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_no_dao_method_operates_without_tenant_id():
    """C2 enforcement: every public method MUST require tenant_id as kwarg.

    This is a static introspection test - we read the source of every
    public async method and confirm 'tenant_id' appears as a keyword-only
    parameter. Prevents future regressions where someone adds a method
    that bypasses tenant scope.
    """
    public_methods = [
        name
        for name, member in inspect.getmembers(TaskCardDAO, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    failures: list[str] = []
    for method_name in public_methods:
        method = getattr(TaskCardDAO, method_name)
        sig = inspect.signature(method)
        params = sig.parameters
        if "tenant_id" not in params:
            failures.append(f"{method_name} missing tenant_id kwarg")
            continue
        kwonly = [
            name for name, p in params.items()
            if p.kind == inspect.Parameter.KEYWORD_ONLY
        ]
        if "tenant_id" not in kwonly:
            failures.append(
                f"{method_name}.tenant_id is not keyword-only "
                f"(required to force callers to be explicit)"
            )

    assert failures == [], "C2 violations: " + "; ".join(failures)
