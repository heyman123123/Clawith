"""Unit tests for group Run failure classification and notify copy."""

from __future__ import annotations

import uuid

from app.services.group_run_resume.service import build_notify_content, classify_failure, error_from_lifecycle


def test_classify_model_call_failed_as_quota() -> None:
    assert classify_failure(error_code="model_call_failed", error_summary="provider 429") == "model_quota"


def test_classify_other_errors_as_general() -> None:
    assert classify_failure(error_code="tool_timeout", error_summary="timed out") == "general"


def test_error_from_lifecycle_reads_code_and_message() -> None:
    code, summary = error_from_lifecycle(
        {"status": "failed", "error": {"code": "model_call_failed", "message": "quota exceeded"}}
    )
    assert code == "model_call_failed"
    assert "quota" in summary


def test_notify_content_never_promises_auto_resume() -> None:
    run_id = uuid.uuid4()
    text = build_notify_content(
        kind="model_quota",
        phase="recovered",
        run_id=run_id,
        error_code="model_call_failed",
        error_summary="provider error",
        agent_name="Leader",
    )
    assert str(run_id) in text
    assert "不会自动续跑" in text
    assert "决定是否重试" in text
