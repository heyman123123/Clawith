"""Dispatch structured decision records to project groups and advance Task DAG."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.governance import DecisionRecord
from app.models.participant import Participant
from app.models.project import ProjectWorkflow
from app.models.task import Task
from app.services import group_message_service
from app.services.decision_sync_content import build_decision_sync_content, decision_summary_ready_for_task_dispatch
from app.services.project_decision_dag import (
    apply_cancelled_task_ids,
    collect_unblocked_tasks,
)
from app.services.task_executor import enqueue_task_runtime


async def _resolve_project_leader_participant(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
) -> Participant | None:
    if workflow.group_leader_agent_id is None:
        return None
    return await db.scalar(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id == workflow.group_leader_agent_id,
        )
    )


async def _apply_new_tasks(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    record: DecisionRecord,
    new_tasks: list[Any],
    creator_id: uuid.UUID,
) -> list[Task]:
    created: list[Task] = []
    for item in new_tasks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "决策新增任务").strip()
        if not title:
            continue
        dependencies = [str(dep) for dep in (item.get("dependencies") or [])]
        task = Task(
            id=uuid.uuid4(),
            agent_id=workflow.group_leader_agent_id,
            title=title,
            description=str(item.get("assigned_role") or ""),
            type="todo",
            status="blocked" if dependencies else "pending",
            priority="high",
            created_by=creator_id,
            project_workflow_id=workflow.id,
            group_id=record.project_group_id,
            session_id=record.project_session_id,
            dependency_task_ids=dependencies,
            report_to_agent_id=workflow.group_leader_agent_id,
        )
        db.add(task)
        created.append(task)
    if created:
        await db.flush()
    return created


async def _enqueue_ready_tasks(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tasks: list[Task],
    tasks_by_id: dict[str, Task],
) -> None:
    agents = {
        agent.id: agent
        for agent in (
            await db.execute(
                select(Agent).where(Agent.id.in_({task.agent_id for task in tasks}))
            )
        ).scalars().all()
    }
    for candidate in collect_unblocked_tasks(tasks, tasks_by_id):
        agent = agents.get(candidate.agent_id)
        if agent is None:
            candidate.status = "failed"
            continue
        candidate.status = "pending"
        await enqueue_task_runtime(db, task=candidate, agent=agent)


async def dispatch_decision_to_project_leader(
    db: AsyncSession,
    *,
    record_id: uuid.UUID,
) -> None:
    """Sync a decision record to the project group and apply DAG changes."""
    record = await db.get(DecisionRecord, record_id)
    if record is None:
        logger.warning("[DecisionDispatcher] Record {} not found", record_id)
        return
    if record.status != "dispatched":
        return

    workflow = await db.get(ProjectWorkflow, record.workflow_id)
    if workflow is None:
        logger.warning("[DecisionDispatcher] Workflow missing for record {}", record_id)
        return

    leader_participant = await _resolve_project_leader_participant(db, workflow=workflow)
    if leader_participant is None:
        logger.warning("[DecisionDispatcher] Project leader participant missing for {}", record_id)
        return

    summary = record.decision_summary or {}
    if not decision_summary_ready_for_task_dispatch(summary):
        logger.info(
            "[DecisionDispatcher] Skipping task mutation for non-dispatchable summary on record {}",
            record_id,
        )
        return

    content = build_decision_sync_content(record_id=record.id, summary=summary)
    await group_message_service.enqueue_group_message(
        db,
        tenant_id=workflow.tenant_id,
        group_id=record.project_group_id,
        session_id=record.project_session_id,
        sender_participant_id=leader_participant.id,
        content=content,
        mention_participant_ids=[leader_participant.id],
        message_id=uuid.uuid5(record.id, "decision-sync"),
        project_task_dispatch=False,
    )

    tasks = list(
        (
            await db.execute(select(Task).where(Task.project_workflow_id == workflow.id))
        ).scalars().all()
    )
    tasks_by_id = {str(task.id): task for task in tasks}
    apply_cancelled_task_ids(tasks_by_id, summary.get("cancelled_tasks") or [])
    created_tasks = await _apply_new_tasks(
        db,
        workflow=workflow,
        record=record,
        new_tasks=summary.get("new_tasks") or [],
        creator_id=workflow.creator_id,
    )
    for task in created_tasks:
        tasks_by_id[str(task.id)] = task
        tasks.append(task)

    await _enqueue_ready_tasks(db, workflow_id=workflow.id, tasks=tasks, tasks_by_id=tasks_by_id)

    record.status = "completed"
    record.completed_at = datetime.now(UTC)
    await db.flush()
