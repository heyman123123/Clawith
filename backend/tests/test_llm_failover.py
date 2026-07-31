"""Transport-error classification contracts for LLM failover."""

import httpx
import pytest

from app.services.llm.failover import FailoverErrorType, classify_error


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout(""),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ],
)
def test_httpx_transport_errors_are_retryable_even_without_timeout_text(
    error: Exception,
) -> None:
    assert classify_error(error) is FailoverErrorType.RETRYABLE
