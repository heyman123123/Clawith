"""Classify terminal run error codes for user-facing Retry eligibility."""

from __future__ import annotations

from typing import Any

_RETRYABLE_CODES = frozenset(
    {
        "model_tool_protocol_violation",
        "finish_protocol_violation",
        "invalid_tool_call_protocol_violation",
        "llm_timeout",
        "llm_unavailable",
        "llm_rate_limit",
        "model_provider_unavailable",
    }
)

_NON_RETRYABLE_CODES = frozenset(
    {
        "llm_auth_failed",
        "llm_invalid_request",
        "data_validation_failed",
    }
)


def is_retryable_error_code(code: str | None) -> bool:
    if not code:
        return False
    if code in _NON_RETRYABLE_CODES:
        return False
    if code in _RETRYABLE_CODES:
        return True
    if code.endswith("_protocol_violation"):
        return True
    return False


def mark_run_failure_retryability(run: Any, error_code: str | None) -> None:
    """Mutate AgentRun.failed_retryable from a terminal error code."""
    run.failed_retryable = is_retryable_error_code(error_code)
