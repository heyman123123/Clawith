from app.services.hr_review_session_service import HrReviewError, _json_object
import pytest


def test_json_object_parses_fenced_json():
    payload = _json_object('```json\n{"proposals":[{"id":"p1"}]}\n```')
    assert payload["proposals"][0]["id"] == "p1"


def test_json_object_strips_think_blocks():
    payload = _json_object(
        '<think>reasoning here</think>\n{"proposals":[{"id":"p2"}]}\n'
    )
    assert payload["proposals"][0]["id"] == "p2"


def test_json_object_empty_raises():
    with pytest.raises(HrReviewError, match="未返回内容"):
        _json_object("   ")


def test_json_object_missing_braces_raises():
    with pytest.raises(HrReviewError, match="未返回有效 JSON"):
        _json_object("sorry I cannot help")
