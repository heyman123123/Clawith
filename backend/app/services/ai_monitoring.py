"""Best-effort, redacted audit telemetry for every central LLM provider call."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, select

from app.database import async_session
from app.models.agent import Agent
from app.models.ai_interaction import AIInteractionLog
from app.services.token_tracker import (
    TokenUsage,
    estimate_token_usage_from_chars,
    extract_token_usage,
)

_SCOPE: ContextVar[AIInteractionScope | None] = ContextVar("ai_interaction_scope", default=None)
_SECRET_KEY = re.compile(r"(api[_-]?key|authorization|cookie|password|secret|token|credential)", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_MAX_TEXT = 12_000
_RETENTION = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class AIInteractionScope:
    tenant_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    llm_model_id: uuid.UUID | None = None
    session_id: str | None = None
    run_id: str | None = None
    source: str = "llm"


@contextmanager
def ai_interaction_scope(**values: Any) -> Iterator[None]:
    """Attach product identity to nested LLM calls without changing provider interfaces."""
    token = _SCOPE.set(AIInteractionScope(**values))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _bounded(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return value if len(value) <= _MAX_TEXT else f"{value[:_MAX_TEXT]}… [TRUNCATED]"


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively remove credentials and bound persisted diagnostic payloads."""
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return _bounded(value)
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded(str(value))


def messages_snapshot(messages: Sequence[Any], tools: Sequence[dict] | None) -> dict:
    return redact(
        {
            "messages": [
                {
                    "role": getattr(message, "role", None),
                    "content": getattr(message, "content", None),
                    "dynamic_content": getattr(message, "dynamic_content", None),
                    "tool_calls": getattr(message, "tool_calls", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                }
                for message in messages
            ],
            "tools": list(tools or []),
        }
    )


def usage_from_provider_or_estimate(
    usage_payload: dict | None,
    messages: Sequence[Any],
    response_content: str | None,
) -> tuple[TokenUsage, bool]:
    """Normalize provider usage, falling back to a bounded content estimate."""
    provider_usage = extract_token_usage(usage_payload)
    if provider_usage is not None:
        return provider_usage, True
    request_chars = sum(len(str(getattr(message, "content", "") or "")) for message in messages)
    input_usage = estimate_token_usage_from_chars(request_chars)
    output_usage = estimate_token_usage_from_chars(len(response_content or ""))
    total_tokens = input_usage.total_tokens + output_usage.total_tokens
    return (
        TokenUsage(
            total_tokens=total_tokens,
            input_tokens=input_usage.total_tokens,
            output_tokens=output_usage.total_tokens,
            estimated_tokens=total_tokens,
        ),
        False,
    )


async def _tenant_id(scope: AIInteractionScope, agent_id: uuid.UUID | None) -> uuid.UUID | None:
    if scope.tenant_id is not None:
        return scope.tenant_id
    if agent_id is None:
        return None
    async with async_session() as db:
        result = await db.execute(select(Agent.tenant_id).where(Agent.id == agent_id))
        return result.scalar_one_or_none()


async def record_ai_interaction(
    *,
    model: Any,
    messages: Sequence[Any],
    tools: Sequence[dict] | None,
    invocation_kind: str,
    usage: TokenUsage | None = None,
    provider_usage_available: bool = False,
    response_content: str | None = None,
    error: Exception | None = None,
    started_at: float | None = None,
    agent_id: uuid.UUID | None = None,
    source: str | None = None,
    session_id: str | None = None,
) -> None:
    """Persist a monitoring event without ever affecting the product request outcome."""
    try:
        scope = _SCOPE.get() or AIInteractionScope(
            agent_id=agent_id,
            session_id=session_id,
            source=source or "llm",
        )
        interaction_agent_id = scope.agent_id
        tenant_id = await _tenant_id(scope, interaction_agent_id)
        if tenant_id is None:
            logger.debug("[AI Monitor] skipped unscoped LLM interaction")
            return
        normalized_usage = usage or TokenUsage()
        token_source = "provider" if provider_usage_available else (
            "estimated" if normalized_usage.estimated_tokens else "unavailable"
        )
        error_payload = None
        if error is not None:
            error_payload = redact(
                {"type": type(error).__name__, "message": str(error), "retryable": None}
            )
        async with async_session() as db:
            finished_at = datetime.now(UTC)
            duration_ms = (
                int((time.monotonic() - started_at) * 1000)
                if started_at
                else None
            )
            started_at_timestamp = finished_at - timedelta(milliseconds=duration_ms or 0)
            db.add(
                AIInteractionLog(
                    tenant_id=tenant_id,
                    agent_id=interaction_agent_id,
                    llm_model_id=scope.llm_model_id or getattr(model, "id", None),
                    session_id=scope.session_id,
                    run_id=scope.run_id,
                    source=scope.source,
                    invocation_kind=invocation_kind,
                    provider=str(getattr(model, "provider", "unknown")),
                    model_name=str(getattr(model, "model", "unknown")),
                    status="error" if error else "success",
                    token_source=token_source,
                    input_tokens=normalized_usage.input_tokens,
                    output_tokens=normalized_usage.output_tokens,
                    cache_read_tokens=normalized_usage.cache_read_tokens,
                    cache_creation_tokens=normalized_usage.cache_creation_tokens,
                    total_tokens=normalized_usage.total_tokens,
                    estimated_tokens=normalized_usage.estimated_tokens,
                    duration_ms=duration_ms,
                    request_context=messages_snapshot(messages, tools),
                    response_content=redact(response_content) if response_content else None,
                    error=error_payload,
                    started_at=started_at_timestamp,
                    finished_at=finished_at,
                    expires_at=datetime.now(UTC) + _RETENTION,
                )
            )
            await db.commit()
    except Exception as monitor_error:  # noqa: BLE001 - telemetry must never affect AI calls.
        logger.warning("[AI Monitor] failed to persist interaction: {}", monitor_error)


async def purge_expired_ai_interactions() -> int:
    """Delete expired audit rows. Safe for a periodic scheduler invocation."""
    async with async_session() as db:
        result = await db.execute(
            delete(AIInteractionLog).where(AIInteractionLog.expires_at < datetime.now(UTC))
        )
        await db.commit()
        return int(result.rowcount or 0)
