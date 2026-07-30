"""Regression coverage for redacted, best-effort AI interaction telemetry."""

from __future__ import annotations

import importlib.util
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import ai_monitoring as ai_monitoring_api
from app.services import ai_monitoring
from app.services.llm import client as llm_client
from app.services.llm.client import LLMMessage
from app.services.token_tracker import TokenUsage


def _migration_module():
    path = Path(__file__).parents[1] / "alembic/versions/202607301300_add_ai_monitoring_logs.py"
    spec = importlib.util.spec_from_file_location("ai_monitoring_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_redact_removes_credentials_from_nested_context_and_bearer_values() -> None:
    payload = ai_monitoring.redact(
        {
            "api_key": "should-not-appear",
            "nested": {"authorization": "Bearer hidden-token", "text": "Bearer abc.def"},
            "messages": ["safe"],
        }
    )

    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["authorization"] == "[REDACTED]"
    assert payload["nested"]["text"] == "Bearer [REDACTED]"


def test_usage_prefers_provider_counters_and_marks_estimates() -> None:
    messages = [LLMMessage(role="user", content="hello")]
    provider_usage, from_provider = ai_monitoring.usage_from_provider_or_estimate(
        {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        messages,
        "world",
    )
    estimated_usage, from_estimate = ai_monitoring.usage_from_provider_or_estimate(
        None,
        messages,
        "world",
    )

    assert from_provider is True
    assert provider_usage == TokenUsage(total_tokens=12, input_tokens=5, output_tokens=7)
    assert from_estimate is False
    assert estimated_usage.total_tokens > 0
    assert estimated_usage.estimated_tokens == estimated_usage.total_tokens


@pytest.mark.asyncio
async def test_convenience_client_records_a_scoped_success(monkeypatch) -> None:
    response = SimpleNamespace(
        content="done",
        tool_calls=[],
        finish_reason="stop",
        usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        model="test-model",
    )
    client = SimpleNamespace(complete=AsyncMock(return_value=response), close=AsyncMock())
    recorded = AsyncMock()
    monkeypatch.setattr(llm_client, "create_llm_client", Mock(return_value=client))
    monkeypatch.setattr(llm_client, "record_ai_interaction", recorded)

    await llm_client.chat_complete(
        provider="openai",
        api_key="test-key",
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert client.close.await_count == 1
    assert recorded.await_count == 1
    assert recorded.await_args.kwargs["provider_usage_available"] is True
    assert recorded.await_args.kwargs["usage"].total_tokens == 3


@pytest.mark.asyncio
async def test_unscoped_interaction_is_skipped_without_a_database_write(monkeypatch) -> None:
    opened_session = AsyncMock()
    monkeypatch.setattr(ai_monitoring, "async_session", opened_session)

    await ai_monitoring.record_ai_interaction(
        model=SimpleNamespace(id=None, provider="openai", model="test"),
        messages=[LLMMessage(role="user", content="hello")],
        tools=None,
        invocation_kind="complete",
    )

    opened_session.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_write_is_redacted_and_never_depends_on_caller_session(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    model_id = uuid.uuid4()
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())

    @asynccontextmanager
    async def session_factory():
        yield db

    monkeypatch.setattr(ai_monitoring, "async_session", session_factory)
    monkeypatch.setattr(ai_monitoring, "_tenant_id", AsyncMock(return_value=tenant_id))

    with ai_monitoring.ai_interaction_scope(
        tenant_id=tenant_id,
        llm_model_id=model_id,
        source="test",
    ):
        await ai_monitoring.record_ai_interaction(
            model=SimpleNamespace(id=None, provider="openai", model="test"),
            messages=[LLMMessage(role="user", content="Bearer sensitive-value")],
            tools=None,
            invocation_kind="complete",
            usage=TokenUsage(total_tokens=3, input_tokens=2, output_tokens=1),
            provider_usage_available=True,
            response_content="Bearer response-secret",
        )

    assert db.add.call_count == 1
    assert db.commit.await_count == 1
    record = db.add.call_args.args[0]
    assert record.tenant_id == tenant_id
    assert record.llm_model_id == model_id
    assert record.token_source == "provider"
    assert record.started_at <= record.finished_at
    assert record.request_context["messages"][0]["content"] == "Bearer [REDACTED]"
    assert record.response_content == "Bearer [REDACTED]"


def test_read_api_is_admin_protected_and_migration_id_is_legacy_safe() -> None:
    route_dependencies: dict[str, list[Any]] = {
        route.path: route.dependencies for route in ai_monitoring_api.router.routes
    }
    dependencies = [
        dependency.call
        for route in ai_monitoring_api.router.routes
        for dependency in route.dependant.dependencies
    ]
    migration = _migration_module()

    assert "/api/ai-monitoring/overview" in route_dependencies
    assert "/api/ai-monitoring/groups/{group_id}/interactions" in route_dependencies
    assert ai_monitoring_api.get_current_admin in dependencies
    assert migration.down_revision == "team_builder_leader"
    assert len(migration.revision) <= 32


def test_migration_resumes_when_monitoring_table_already_exists(monkeypatch) -> None:
    migration = _migration_module()
    created_tables: list[str] = []
    created_indexes: list[str] = []
    operations = SimpleNamespace(
        create_table=lambda name, *_args: created_tables.append(name),
        create_index=lambda name, *_args: created_indexes.append(name),
    )
    inspector = SimpleNamespace(
        has_table=lambda name: name == "ai_interaction_logs",
        get_indexes=lambda _name: [
            {"name": "ix_ai_interaction_logs_tenant_created"},
            {"name": "ix_ai_interaction_logs_tenant_status_created"},
            {"name": "ix_ai_interaction_logs_agent_created"},
        ],
    )
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_inspector", lambda: inspector)

    migration.upgrade()

    assert created_tables == []
    assert created_indexes == ["ix_ai_interaction_logs_expires_at"]
