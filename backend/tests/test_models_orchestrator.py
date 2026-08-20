"""Tests for orchestrator SQLModel classes."""
from app.models.draft import Draft
from app.models.task_card import TaskCard
from app.models.task_card_dependency import TaskCardDependency
from app.models.task_board_event import TaskBoardEvent
from app.models.chief_run import ChiefRun
from app.models.agent import AgentTemplate
from app.models.chat_session import ChatSession


def test_draft_model_has_required_fields():
    fields = {c.name for c in Draft.__table__.columns}
    assert {"id", "tenant_id", "user_id", "user_message", "draft_payload",
            "template_visibility", "status", "created_at", "updated_at"} <= fields


def test_task_card_has_optimistic_lock_and_column():
    fields = {c.name for c in TaskCard.__table__.columns}
    assert {"version", "column", "tenant_id", "group_id", "assignee_agent_id",
            "okr_key_result_id", "artifact_paths", "target_window_start",
            "target_window_end"} <= fields


def test_task_card_dependency_has_dag_pair():
    fields = {c.name for c in TaskCardDependency.__table__.columns}
    assert {"task_card_id", "depends_on_card_id", "dependency_type"} <= fields


def test_task_board_event_supports_chief_consumption():
    fields = {c.name for c in TaskBoardEvent.__table__.columns}
    assert {"group_id", "task_card_id", "event_type", "payload",
            "actor_id", "actor_type", "occurred_at"} <= fields


def test_chief_run_has_one_to_one_group():
    fields = {c.name for c in ChiefRun.__table__.columns}
    assert {"group_id", "chief_agent_id", "runtime_thread_id", "status",
            "failure_count", "last_failure_reason"} <= fields


def test_agent_template_extended_with_visibility():
    fields = {c.name for c in AgentTemplate.__table__.columns}
    assert {"template_visibility", "tenant_id", "approval_state", "rejected_reason"} <= fields


def test_chat_session_extended_with_chief_run_id():
    fields = {c.name for c in ChatSession.__table__.columns}
    assert "chief_run_id" in fields
