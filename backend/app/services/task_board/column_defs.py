"""Task board column state machine (Kanban semantics).

Five canonical columns with constrained transitions:

  backlog     → in_progress, blocked
  in_progress → blocked, review, backlog
  blocked     → backlog, in_progress
  review      → done, in_progress, blocked
  done        → (terminal)

The state machine rejects illegal moves with ValueError so callers get a
clean error rather than silently corrupting board state.
"""

from __future__ import annotations

from enum import Enum


class TaskColumn(str, Enum):
    """Kanban column names. Stored as VARCHAR in DB."""

    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"


_ALLOWED_TRANSITIONS: dict[TaskColumn, frozenset[TaskColumn]] = {
    TaskColumn.BACKLOG: frozenset({TaskColumn.IN_PROGRESS, TaskColumn.BLOCKED}),
    TaskColumn.IN_PROGRESS: frozenset(
        {TaskColumn.BLOCKED, TaskColumn.REVIEW, TaskColumn.BACKLOG}
    ),
    TaskColumn.BLOCKED: frozenset({TaskColumn.BACKLOG, TaskColumn.IN_PROGRESS}),
    TaskColumn.REVIEW: frozenset({TaskColumn.DONE, TaskColumn.IN_PROGRESS, TaskColumn.BLOCKED}),
    TaskColumn.DONE: frozenset(),  # terminal
}


def validate_transition(from_col: TaskColumn, to_col: TaskColumn) -> bool:
    """Return True iff the transition is legal.

    Same-column moves are always allowed (no-op semantics for the caller).
    """
    if from_col == to_col:
        return True
    return to_col in _ALLOWED_TRANSITIONS.get(from_col, frozenset())


def assert_transition(from_col: TaskColumn, to_col: TaskColumn) -> None:
    """Raise ValueError on illegal transition; useful for FastAPI handlers."""
    if not validate_transition(from_col, to_col):
        allowed = sorted(c.value for c in _ALLOWED_TRANSITIONS[from_col])
        raise ValueError(
            f"Illegal transition: {from_col.value} -> {to_col.value}. "
            f"Allowed next states from {from_col.value}: {allowed}"
        )


def all_columns() -> list[TaskColumn]:
    """Return all canonical columns in board order."""
    return [
        TaskColumn.BACKLOG,
        TaskColumn.IN_PROGRESS,
        TaskColumn.BLOCKED,
        TaskColumn.REVIEW,
        TaskColumn.DONE,
    ]
