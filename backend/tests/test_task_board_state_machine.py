"""T-011: TaskBoard column state machine tests (full coverage)."""
from app.services.task_board.column_defs import TaskColumn, validate_transition, assert_transition, all_columns
import pytest


def test_all_legal_transitions_are_valid():
    """Every transition in the documented allowed-set is legal."""
    legal = [
        (TaskColumn.BACKLOG, TaskColumn.IN_PROGRESS),
        (TaskColumn.BACKLOG, TaskColumn.BLOCKED),
        (TaskColumn.IN_PROGRESS, TaskColumn.BLOCKED),
        (TaskColumn.IN_PROGRESS, TaskColumn.REVIEW),
        (TaskColumn.IN_PROGRESS, TaskColumn.BACKLOG),
        (TaskColumn.BLOCKED, TaskColumn.BACKLOG),
        (TaskColumn.BLOCKED, TaskColumn.IN_PROGRESS),
        (TaskColumn.REVIEW, TaskColumn.DONE),
        (TaskColumn.REVIEW, TaskColumn.IN_PROGRESS),
        (TaskColumn.REVIEW, TaskColumn.BLOCKED),
    ]
    for f, t in legal:
        assert validate_transition(f, t) is True, f"{f.value}->{t.value} should be legal"


def test_documented_illegal_transitions_are_rejected():
    illegal = [
        (TaskColumn.DONE, TaskColumn.IN_PROGRESS),
        (TaskColumn.DONE, TaskColumn.REVIEW),
        (TaskColumn.BACKLOG, TaskColumn.DONE),
        (TaskColumn.BACKLOG, TaskColumn.REVIEW),
    ]
    for f, t in illegal:
        assert validate_transition(f, t) is False, f"{f.value}->{t.value} should be illegal"


def test_assert_transition_raises_with_helpful_message():
    with pytest.raises(ValueError) as exc_info:
        assert_transition(TaskColumn.DONE, TaskColumn.IN_PROGRESS)
    msg = str(exc_info.value)
    assert "Illegal transition" in msg
    assert "done" in msg
