from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.hr_review import HrReviewSession


def test_group_has_group_type_column():
    assert hasattr(Group, "group_type")


def test_chat_session_has_parent_session_id():
    assert hasattr(ChatSession, "parent_session_id")


def test_hr_review_session_model_fields():
    assert HrReviewSession.__tablename__ == "hr_review_sessions"
    for field in (
        "group_id",
        "session_id",
        "session_type",
        "status",
        "proposals",
        "selected_proposal_id",
        "context_payload",
        "created_at",
        "closed_at",
    ):
        assert hasattr(HrReviewSession, field)
