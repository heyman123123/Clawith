"""Tests for Tenant.default_model_id backfill into Agent.primary_model_id."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.llm import default_propagation


@dataclass
class _StubLLMModel:
    """Minimal stand-in for LLMModel."""

    id: uuid.UUID
    enabled: bool = True
    deleted_at: Any = None


@dataclass
class _StubAgent:
    """Minimal stand-in for Agent."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    primary_model_id: uuid.UUID | None = None
    deleted_at: Any = None


@dataclass
class _StubTenant:
    id: uuid.UUID
    is_active: bool = True
    default_model_id: uuid.UUID | None = None


@dataclass
class _StubUpdateResult:
    """Result wrapper for update(...).execute() that exposes rowcount."""

    rowcount: int = 0


class _StubScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _StubSession:
    """Hand-rolled AsyncSession stub.

    Three kinds of DB calls happen inside the production code:
    * ``db.scalar(select(...))`` — used twice per tenant: first to read
      ``Tenant.default_model_id``, then a re-read of the same value plus
      the ``LLMModel`` usability probe inside ``_tenant_default_model_is_usable``.
    * ``db.scalars(select(...))`` — used once at the start of the all-tenants
      sweep to enumerate active tenant ids.
    * ``db.execute(update(...))`` — used to actually backfill agents.

    Each call consumes one entry from the corresponding queue in declared order.
    """

    def __init__(
        self,
        *,
        scalar_responses: list[Any] | None = None,
        scalars_responses: list[Any] | None = None,
        update_responses: list[int] | None = None,
    ) -> None:
        self._scalar_responses = list(scalar_responses or [])
        self._scalars_responses = list(scalars_responses or [])
        self._update_responses = list(update_responses or [])
        self.commit_count = 0
        self.executed: list[Any] = []

    async def scalar(self, stmt):
        self.executed.append(stmt)
        if not self._scalar_responses:
            return None
        return self._scalar_responses.pop(0)

    async def scalars(self, stmt):
        self.executed.append(stmt)
        if not self._scalars_responses:
            return _StubScalars([])
        return _StubScalars(self._scalars_responses.pop(0))

    async def execute(self, stmt):
        self.executed.append(stmt)
        if self._is_update_stmt(stmt):
            rowcount = self._update_responses.pop(0) if self._update_responses else 0
            return _StubUpdateResult(rowcount=rowcount)
        if not self._scalar_responses:
            return None
        return self._scalar_responses.pop(0)

    async def commit(self) -> None:
        self.commit_count += 1

    @staticmethod
    def _is_update_stmt(stmt: Any) -> bool:
        from sqlalchemy.sql.dml import Update  # local import keeps tests light

        return isinstance(stmt, Update)


def _clause_targets_column(clause, column) -> bool:
    """Return True when a SQLAlchemy BooleanClauseList contains a check on the given column."""
    items = list(getattr(clause, "clauses", [])) if hasattr(clause, "clauses") else []
    if not items:
        items = list(getattr(clause, "children", []))
    for child in items:
        left = getattr(child, "left", None)
        if (
            left is not None
            and getattr(left, "key", None) == column.key
            and getattr(getattr(left, "table", None), "name", None) == column.table.name
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# 1. Tenant has no default → returns 0 and never writes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_default_returns_zero_and_does_not_write():
    tenant_id = uuid.uuid4()
    # First scalar returns None (Tenant.default_model_id is NULL).
    # No further calls should be made.
    session = _StubSession(scalar_responses=[None])

    count = await default_propagation.propagate_tenant_default_to_unassigned_agents(
        session,  # type: ignore[arg-type]
        tenant_id,
    )

    assert count == 0
    assert len(session.executed) == 1
    assert all(not _StubSession._is_update_stmt(s) for s in session.executed)


# ---------------------------------------------------------------------------
# 2. Tenant has a default and agent.primary_model_id IS NULL → backfilled.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unassigned_agents_get_default_model_id():
    tenant_id = uuid.uuid4()
    default_model_id = uuid.uuid4()
    # Order of scalar calls per tenant:
    #   1) Tenant.default_model_id
    #   2) Tenant.default_model_id re-read inside _tenant_default_model_is_usable
    #   3) LLMModel usability probe
    session = _StubSession(
        scalar_responses=[
            default_model_id,
            default_model_id,
            _StubLLMModel(id=default_model_id),
        ],
        update_responses=[3],
    )

    count = await default_propagation.propagate_tenant_default_to_unassigned_agents(
        session,  # type: ignore[arg-type]
        tenant_id,
    )

    assert count == 3
    update_stmt = session.executed[-1]
    assert _StubSession._is_update_stmt(update_stmt)
    compiled_params = update_stmt.compile()
    compiled_params_dict = dict(compiled_params.params)
    # SQLAlchemy binds parameters positionally; the value we wrote is the
    # primary_model_id we passed into ``.values(primary_model_id=...)``.
    assert default_model_id in compiled_params_dict.values()


# ---------------------------------------------------------------------------
# 3. Tenant has default but agent.primary_model_id is already set → rowcount == 0.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_assigned_agents_unchanged():
    tenant_id = uuid.uuid4()
    default_model_id = uuid.uuid4()
    # Update matches 0 rows when every agent already has a model set.
    session = _StubSession(
        scalar_responses=[
            default_model_id,
            default_model_id,
            _StubLLMModel(id=default_model_id),
        ],
        update_responses=[0],
    )

    count = await default_propagation.propagate_tenant_default_to_unassigned_agents(
        session,  # type: ignore[arg-type]
        tenant_id,
    )

    assert count == 0


# ---------------------------------------------------------------------------
# 4. Soft-deleted agents must be skipped (UPDATE filters deleted_at IS NULL).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_deleted_agents_excluded_from_update():
    tenant_id = uuid.uuid4()
    default_model_id = uuid.uuid4()
    session = _StubSession(
        scalar_responses=[
            default_model_id,
            default_model_id,
            _StubLLMModel(id=default_model_id),
        ],
        update_responses=[2],
    )

    count = await default_propagation.propagate_tenant_default_to_unassigned_agents(
        session,  # type: ignore[arg-type]
        tenant_id,
    )

    assert count == 2
    update_stmt = session.executed[-1]
    where_clause = update_stmt.whereclause
    from app.models.agent import Agent

    has_deleted_filter = _clause_targets_column(where_clause, Agent.deleted_at)
    assert has_deleted_filter


# ---------------------------------------------------------------------------
# 5. Tenant default model is deleted or disabled → no write happens.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_default_model_deleted_or_disabled():
    tenant_id = uuid.uuid4()
    default_model_id = uuid.uuid4()
    # 1) Tenant.default_model_id probe returns the id;
    # 2) Tenant.default_model_id re-probe returns the id again;
    # 3) LLMModel probe returns None (deleted or disabled).
    session = _StubSession(
        scalar_responses=[default_model_id, default_model_id, None],
    )

    count = await default_propagation.propagate_tenant_default_to_unassigned_agents(
        session,  # type: ignore[arg-type]
        tenant_id,
    )

    assert count == 0
    assert len(session.executed) == 3
    assert all(not _StubSession._is_update_stmt(s) for s in session.executed)


# ---------------------------------------------------------------------------
# 6. All-tenants sweep returns {tenant_id: count} aggregated from each tenant.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propagate_all_tenants_aggregates_per_tenant_counts():
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    tenant_c = uuid.uuid4()
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()

    # Queue ordering:
    # 1) scalars(select(Tenant.id)) returns the active tenant ids.
    # 2) Per tenant scalars: default_id → re-default_id → LLMModel probe
    #    (tenant_c short-circuits because default_id is None).
    # 3) Update rowcounts only for tenants a & b.
    session = _StubSession(
        scalars_responses=[[tenant_a, tenant_b, tenant_c]],
        scalar_responses=[
            model_a, model_a, _StubLLMModel(id=model_a),  # tenant_a
            model_b, model_b, _StubLLMModel(id=model_b),  # tenant_b
            None,  # tenant_c: default_id None short-circuits
        ],
        update_responses=[2, 1],
    )

    summary = await default_propagation.propagate_tenant_default_all_tenants(
        session,  # type: ignore[arg-type]
    )

    assert summary[str(tenant_a)] == 2
    assert summary[str(tenant_b)] == 1
    assert summary[str(tenant_c)] == 0
    assert sum(summary.values()) == 3