"""Pure Task DAG helpers for governance decision dispatch."""

from __future__ import annotations

from typing import Any

from app.models.task import Task


def dependencies_done(task: Task, tasks_by_id: dict[str, Task]) -> bool:
    ids = task.dependency_task_ids or []
    if not ids:
        return True
    return all(
        (dependency := tasks_by_id.get(str(task_id))) is not None and dependency.status == "done"
        for task_id in ids
    )


def apply_cancelled_task_ids(
    tasks_by_id: dict[str, Task],
    cancelled_ids: list[Any],
) -> list[str]:
    """Mark tasks failed (cancel semantics); return titles of tasks that changed."""
    changed: list[str] = []
    for raw_id in cancelled_ids:
        task = tasks_by_id.get(str(raw_id))
        if task is None or task.status in {"cancelled", "failed", "done"}:
            continue
        task.status = "failed"
        changed.append(task.title)
    return changed


def collect_unblocked_tasks(
    tasks: list[Task],
    tasks_by_id: dict[str, Task],
) -> list[Task]:
    """Return blocked tasks whose dependencies are all done."""
    ready: list[Task] = []
    for candidate in tasks:
        if candidate.status != "blocked":
            continue
        if dependencies_done(candidate, tasks_by_id):
            ready.append(candidate)
    return ready
