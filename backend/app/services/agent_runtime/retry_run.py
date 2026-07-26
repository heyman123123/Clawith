"""User-facing failed-run retry: new AgentRun linked via retry_of_run_id."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.user import User


ALLOWED_STRATEGIES = frozenset({"fresh_context", "in_place"})


class RetryRunError(ValueError):
    """Invalid retry request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RetryRunResult:
    run_id: uuid.UUID
    thread_id: str
    command_id: uuid.UUID
    runtime_type: str
    created: bool
    retry_of_run_id: uuid.UUID
    strategy: str


def build_retry_source_execution_id(source_run_id: uuid.UUID) -> str:
    return f"retry:{source_run_id}:{uuid.uuid4().hex[:12]}"


def validate_retry_request(*, failed_retryable: bool | None, strategy: str) -> None:
    if strategy not in ALLOWED_STRATEGIES:
        raise RetryRunError("unsupported_retry_strategy", f"unsupported retry strategy: {strategy}")
    if not failed_retryable:
        raise RetryRunError("run_not_retryable", "run is not retryable")


async def _resolve_failed_retryable(db: AsyncSession, source_run: AgentRun) -> bool | None:
    """Prefer column value; fall back to latest run_failed event for pre-migration rows."""
    if source_run.failed_retryable is not None:
        return source_run.failed_retryable
    from app.models.agent_run_event import AgentRunEvent
    from app.services.agent_runtime.retry_classifier import (
        is_retryable_error_code,
        mark_run_failure_retryability,
    )

    result = await db.execute(
        select(AgentRunEvent)
        .where(
            AgentRunEvent.tenant_id == source_run.tenant_id,
            AgentRunEvent.run_id == source_run.id,
            AgentRunEvent.event_type == "run_failed",
        )
        .order_by(AgentRunEvent.created_at.desc())
        .limit(1)
    )
    event = result.scalar_one_or_none()
    if event is None or not isinstance(event.payload, dict):
        return None
    error_code = event.payload.get("error_code") or event.payload.get("reason")
    if not isinstance(error_code, str):
        return None
    mark_run_failure_retryability(source_run, error_code)
    return source_run.failed_retryable


async def create_retry_run(
    db: AsyncSession,
    *,
    source_run: AgentRun,
    user: User,
    strategy: str,
) -> RetryRunResult:
    """Create a fresh chat run that retries a failed source run.

    Does not commit; caller owns the transaction. MVP implements fresh_context only.
    """
    from app.models.agent import Agent
    from app.models.chat_session import ChatSession
    from app.models.llm import LLMModel
    from app.services.agent_runtime.adapter import RuntimeAdapterError
    from app.services.agent_runtime.chat_intake import (
        ChatRuntimeIntakeError,
        enqueue_chat_runtime,
    )

    failed_retryable = await _resolve_failed_retryable(db, source_run)
    validate_retry_request(failed_retryable=failed_retryable, strategy=strategy)
    if strategy == "in_place":
        raise RetryRunError("retry_strategy_not_implemented", "in_place retry is not implemented yet")

    if source_run.agent_id is None:
        raise RetryRunError("retry_missing_agent", "source run has no agent")
    if source_run.session_id is None:
        raise RetryRunError("retry_missing_session", "source run has no session")
    if source_run.model_id is None:
        raise RetryRunError("retry_missing_model", "source run has no model")
    if source_run.origin_user_id is not None and source_run.origin_user_id != user.id:
        raise RetryRunError("retry_forbidden", "source run does not belong to this user")

    agent = await db.get(Agent, source_run.agent_id)
    if agent is None or agent.tenant_id != source_run.tenant_id or agent.deleted_at is not None:
        raise RetryRunError("agent_unavailable", "agent for source run is unavailable")

    session = await db.get(ChatSession, source_run.session_id)
    if (
        session is None
        or session.tenant_id != source_run.tenant_id
        or session.agent_id != agent.id
        or session.user_id != user.id
    ):
        raise RetryRunError("session_unavailable", "session for source run is unavailable")

    model = await db.get(LLMModel, source_run.model_id)
    if model is None or (model.tenant_id is not None and model.tenant_id != source_run.tenant_id):
        raise RetryRunError("model_unavailable", "model for source run is unavailable")
    if model.deleted_at is not None:
        raise RetryRunError("model_unavailable", "model for source run is unavailable")

    source_execution_id = build_retry_source_execution_id(source_run.id)
    goal = (source_run.goal or "").strip() or "Retry previous request"
    try:
        intake = await enqueue_chat_runtime(
            db,
            agent=agent,
            user=user,
            session=session,
            model=model,
            content=goal,
            persist_user_message=False,
            source_execution_id_override=source_execution_id,
        )
    except ChatRuntimeIntakeError as exc:
        raise RetryRunError(exc.code, str(exc)) from exc
    except RuntimeAdapterError as exc:
        raise RetryRunError(exc.code, str(exc)) from exc

    if intake is None:
        raise RetryRunError("runtime_disabled", "Runtime intake is disabled for this agent")

    new_run = await db.get(AgentRun, intake.handle.run_id)
    if new_run is None:
        raise RetryRunError("retry_run_missing", "retry run was not persisted")
    new_run.retry_of_run_id = source_run.id
    new_run.retry_strategy = strategy
    await db.flush()

    handle = intake.handle
    return RetryRunResult(
        run_id=handle.run_id,
        thread_id=handle.thread_id,
        command_id=handle.command_id,
        runtime_type=handle.runtime_type,
        created=handle.created,
        retry_of_run_id=source_run.id,
        strategy=strategy,
    )


async def load_retryable_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AgentRun | None:
    result = await db.execute(
        select(AgentRun).where(AgentRun.tenant_id == tenant_id, AgentRun.id == run_id)
    )
    return result.scalar_one_or_none()


__all__ = [
    "ALLOWED_STRATEGIES",
    "RetryRunError",
    "RetryRunResult",
    "build_retry_source_execution_id",
    "create_retry_run",
    "load_retryable_run",
    "validate_retry_request",
]
