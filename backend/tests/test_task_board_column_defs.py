"""Tests for TaskColumn state machine."""
import pytest
from app.services.task_board.column_defs import (
    TaskColumn,
    validate_transition,
    assert_transition,
    all_columns,
)


def test_backlog_to_in_progress_is_valid():
    assert validate_transition(TaskColumn.BACKLOG, TaskColumn.IN_PROGRESS) is True


def test_in_progress_to_review_is_valid():
    assert validate_transition(TaskColumn.IN_PROGRESS, TaskColumn.REVIEW) is True


def test_review_to_done_is_valid():
    assert validate_transition(TaskColumn.REVIEW, TaskColumn.DONE) is True


def test_done_to_in_progress_is_invalid():
    assert validate_transition(TaskColumn.DONE, TaskColumn.IN_PROGRESS) is False


def test_backlog_to_done_is_invalid():
    assert validate_transition(TaskColumn.BACKLOG, TaskColumn.DONE) is False


def test_done_is_terminal():
    # No outgoing edges from DONE
    from app.services.task_board.column_defs import _ALLOWED_TRANSITIONS
    assert _ALLOWED_TRANSITIONS[TaskColumn.DONE] == frozenset()


def test_assert_transition_raises_on_illegal():
    with pytest.raises(ValueError, match="Illegal transition"):
        assert_transition(TaskColumn.DONE, TaskColumn.IN_PROGRESS)


def test_same_column_is_no_op():
    for col in all_columns():
        assert validate_transition(col, col) is True


def test_all_columns_count():
    assert len(all_columns()) == 5


def test_column_string_values():
    assert TaskColumn.BACKLOG.value == "backlog"
    assert TaskColumn.IN_PROGRESS.value == "in_progress"
    assert TaskColumn.BLOCKED.value == "blocked"
    assert TaskColumn.REVIEW.value == "review"
    assert TaskColumn.DONE.value == "done"
