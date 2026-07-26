import uuid

import pytest

from app.services.agent_runtime.retry_run import (
    RetryRunError,
    build_retry_source_execution_id,
    validate_retry_request,
)


def test_build_retry_source_execution_id_is_unique_prefix():
    rid = uuid.uuid4()
    a = build_retry_source_execution_id(rid)
    b = build_retry_source_execution_id(rid)
    assert a.startswith(f"retry:{rid}:")
    assert a != b


def test_validate_rejects_non_retryable():
    with pytest.raises(RetryRunError, match="not retryable"):
        validate_retry_request(failed_retryable=False, strategy="fresh_context")


def test_validate_rejects_bad_strategy():
    with pytest.raises(RetryRunError, match="strategy"):
        validate_retry_request(failed_retryable=True, strategy="magic")


def test_validate_accepts_fresh_context():
    validate_retry_request(failed_retryable=True, strategy="fresh_context")
