from types import SimpleNamespace

from app.services.agent_runtime.retry_classifier import (
    is_retryable_error_code,
    mark_run_failure_retryability,
)


def test_protocol_violation_is_retryable():
    assert is_retryable_error_code("model_tool_protocol_violation") is True


def test_llm_timeout_is_retryable():
    assert is_retryable_error_code("llm_timeout") is True


def test_llm_unavailable_and_rate_limit_are_retryable():
    assert is_retryable_error_code("llm_unavailable") is True
    assert is_retryable_error_code("llm_rate_limit") is True


def test_auth_and_invalid_request_are_not_retryable():
    assert is_retryable_error_code("llm_auth_failed") is False
    assert is_retryable_error_code("llm_invalid_request") is False
    assert is_retryable_error_code("data_validation_failed") is False


def test_unknown_and_none_are_not_retryable():
    assert is_retryable_error_code(None) is False
    assert is_retryable_error_code("runtime_failed") is False
    assert is_retryable_error_code("finish_protocol_violation") is True


def test_mark_run_failure_retryability():
    run = SimpleNamespace(failed_retryable=None)
    mark_run_failure_retryability(run, "model_tool_protocol_violation")
    assert run.failed_retryable is True
    mark_run_failure_retryability(run, "llm_auth_failed")
    assert run.failed_retryable is False
