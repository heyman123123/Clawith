"""Milestone progress and status for dependency-driven project workflows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import ProjectMilestone
from app.models.task import Task


_MILESTONES_JSON = re.compile(r"\{[\s\S]*\"milestones\"[\s\S]*\}", re.MULTILINE)


def _tasks_for_milestone(milestone_id: uuid.UUID, tasks: Sequence[Task]) -> list[Task]:
    return [task for task in tasks if task.milestone_id == milestone_id]


def milestone_task_progress(milestone: ProjectMilestone, tasks: Sequence[Task]) -> dict[str, int]:
    """Return completed/total/percent for one milestone's assigned tasks."""
    milestone_tasks = _tasks_for_milestone(milestone.id, tasks)
    total = len(milestone_tasks)
    completed = sum(task.status == "done" for task in milestone_tasks)
    percent = round((completed / total) * 100) if total else 0
    return {"completed": completed, "total": total, "percent": percent}


def milestone_progress(
    milestones: Sequence[ProjectMilestone],
    tasks: Sequence[Task],
) -> dict[str, int]:
    """Return completed/total/percent for workflow-level milestone completion."""
    total = len(milestones)
    completed = sum(milestone.status == "done" for milestone in milestones)
    percent = round((completed / total) * 100) if total else 0
    return {"completed": completed, "total": total, "percent": percent}


def derive_milestone_status(milestone: ProjectMilestone, tasks: Sequence[Task]) -> str:
    """Compute the next milestone status from its assigned tasks."""
    milestone_tasks = _tasks_for_milestone(milestone.id, tasks)
    if not milestone_tasks:
        return milestone.status if milestone.status == "cancelled" else "pending"
    if all(task.status == "done" for task in milestone_tasks):
        return "done"
    if milestone.status == "cancelled":
        return "cancelled"
    return "active"


def parse_milestones_payload(detail: str) -> list[dict]:
    """Extract a milestones array from leader task output when present."""
    detail = detail.strip()
    if not detail:
        return []
    candidates: list[str] = [detail]
    match = _MILESTONES_JSON.search(detail)
    if match is not None:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            raw = payload.get("milestones")
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, Mapping)]
    return []


async def create_milestones_for_workflow(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    items: list[dict],
    agent_id: uuid.UUID | None,
    tasks: Iterable[Task] | None = None,
) -> list[ProjectMilestone]:
    """Create ordered milestones and optionally link tasks by title."""
    if not items:
        return []
    task_list = list(tasks or [])
    tasks_by_title = {task.title.strip(): task for task in task_list if task.title}
    created: list[ProjectMilestone] = []
    for index, item in enumerate(items):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        order_index = item.get("order_index")
        if not isinstance(order_index, int):
            order_index = index
        milestone = ProjectMilestone(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            title=title[:200],
            description=(str(item.get("description")).strip()[:12_000] if item.get("description") else None),
            order_index=order_index,
            status="pending",
            created_by_agent_id=agent_id,
        )
        db.add(milestone)
        created.append(milestone)
        await db.flush()

        linked_task_ids = item.get("task_ids") or item.get("tasks") or item.get("task_titles") or []
        if isinstance(linked_task_ids, list):
            for entry in linked_task_ids:
                task = None
                if isinstance(entry, str):
                    try:
                        task = next((candidate for candidate in task_list if str(candidate.id) == entry), None)
                    except ValueError:
                        task = tasks_by_title.get(entry.strip())
                    if task is None:
                        task = tasks_by_title.get(entry.strip())
                if task is not None:
                    task.milestone_id = milestone.id
    await db.flush()
    return created


async def refresh_milestone_statuses(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Mark milestones done when all assigned tasks are done."""
    milestones = list(
        (
            await db.execute(
                select(ProjectMilestone)
                .where(ProjectMilestone.workflow_id == workflow_id)
                .order_by(ProjectMilestone.order_index.asc())
            )
        ).scalars().all()
    )
    if not milestones:
        return
    tasks = list(
        (
            await db.execute(select(Task).where(Task.project_workflow_id == workflow_id))
        ).scalars().all()
    )
    clock = now or datetime.now(UTC)
    for milestone in milestones:
        next_status = derive_milestone_status(milestone, tasks)
        if next_status == milestone.status:
            continue
        milestone.status = next_status
        if next_status == "done":
            milestone.completed_at = clock
        elif next_status != "done" and milestone.completed_at is not None:
            milestone.completed_at = None


async def ingest_leader_milestones(
    db: AsyncSession,
    *,
    task: Task,
    detail: str,
) -> list[ProjectMilestone]:
    """Create milestones from leader breakdown output once per workflow."""
    if task.project_workflow_id is None:
        return []
    existing = await db.scalar(
        select(ProjectMilestone.id)
        .where(ProjectMilestone.workflow_id == task.project_workflow_id)
        .limit(1)
    )
    if existing is not None:
        return []
    items = parse_milestones_payload(detail)
    if not items:
        return []
    tasks = list(
        (
            await db.execute(select(Task).where(Task.project_workflow_id == task.project_workflow_id))
        ).scalars().all()
    )
    return await create_milestones_for_workflow(
        db,
        workflow_id=task.project_workflow_id,
        items=items,
        agent_id=task.agent_id,
        tasks=tasks,
    )


__all__ = [
    "create_milestones_for_workflow",
    "derive_milestone_status",
    "ingest_leader_milestones",
    "milestone_progress",
    "milestone_task_progress",
    "parse_milestones_payload",
    "refresh_milestone_statuses",
]
