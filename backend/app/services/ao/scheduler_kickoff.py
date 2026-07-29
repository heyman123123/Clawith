"""调度官首发触发器 (Scheduler Kickoff) — P1.4 first-launch entry point.

P1.3 lands the four-power roles, composes the AO YAML, and seeds the
``workflow_run_steps`` rows.  The actual "首发开跑" is intentionally kept
in a dedicated module so the wiring can be tested in isolation and so P2
can extend it without entangling the tool registry.

The kickoff is intentionally **idempotent**: it does not create a new
``WorkflowRun`` row (P2 will). It only:

1. Resolves the project's execution group + scheduler Agent via the
   existing ``ProjectWorkflow`` row.
2. Calls the scheduler tools in the right order to initialise the asset
   directory, fetch the AO plan, stamp the status, and broadcast a public
   "首发开跑" message into the group.
3. Stamps ``started_at`` via ``run_repository.mark_run_started`` so
   dashboards stop showing the workflow as ``provisioning``.

Tests inject a fake DB and monkeypatch every external call.  In
production the call originates from the project provisioning service
once the execution group is ready.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from loguru import logger

from app.services.ao import run_repository
from app.services.ao.scheduler_tools import (
    ao_get_execution_plan,
    init_workflow_dir,
    scheduler_tool_context,
    send_channel_message,
    update_workflow_status,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession



_ESTIMATED_MINUTES_PER_STEP = 5


async def run_scheduler_kickoff(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
) -> dict:
    """Drive the scheduler's first-launch sequence for a workflow.

    Returns a JSON-friendly summary that the Runtime can show in the
    group feed or the "工作流执行" stub.  The function never raises
    ``AOIntegrationError`` upward; instead every step is captured in the
    returned ``steps`` list with its individual ``ok`` flag so the
    scheduler can decide whether to retry.
    """
    from sqlalchemy import select

    from app.models.project import ProjectWorkflow

    workflow = await db.scalar(
        select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id)
    )
    if workflow is None:
        return {"ok": False, "workflow_id": str(workflow_id), "error": "Workflow not found"}
    # Tests inject lightweight ``SimpleNamespace`` rows that may not yet
    # carry every P1.3 column; production ``ProjectWorkflow`` always does.
    group_id = getattr(workflow, "group_id", None)
    leader_agent_id = getattr(workflow, "group_leader_agent_id", None)
    creator_id = getattr(workflow, "creator_id", None)
    if group_id is None or leader_agent_id is None:
        return {
            "ok": False,
            "workflow_id": str(workflow_id),
            "error": "Execution group / leader is not ready for kickoff",
        }

    step_results: list[dict] = []
    with scheduler_tool_context(
        db=db,
        workflow_id=workflow_id,
        actor_agent_id=leader_agent_id,
        user_id=creator_id,
    ):
        try:
            init_result = init_workflow_dir(str(workflow_id))
            step_results.append({"step": "init_workflow_dir", "ok": True, "result": init_result})
        except Exception as exc:  # noqa: BLE001 — surface in the return payload
            logger.exception("[SchedulerKickoff] init_workflow_dir failed for {}", workflow_id)
            step_results.append({"step": "init_workflow_dir", "ok": False, "error": str(exc)})
            return {
                "ok": False,
                "workflow_id": str(workflow_id),
                "steps": step_results,
                "error": "init_workflow_dir failed",
            }

        try:
            plan = ao_get_execution_plan(str(workflow_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[SchedulerKickoff] ao_get_execution_plan failed for {}", workflow_id)
            step_results.append({"step": "ao_get_execution_plan", "ok": False, "error": str(exc)})
            return {
                "ok": False,
                "workflow_id": str(workflow_id),
                "steps": step_results,
                "error": "ao_get_execution_plan failed",
            }
        step_results.append(
            {"step": "ao_get_execution_plan", "ok": True, "result": {"steps_count": len(plan)}}
        )

        try:
            status_result = update_workflow_status(str(workflow_id), "active", note="首发开跑")
        except Exception as exc:  # noqa: BLE001
            logger.exception("[SchedulerKickoff] update_workflow_status failed for {}", workflow_id)
            step_results.append({"step": "update_workflow_status", "ok": False, "error": str(exc)})
            return {
                "ok": False,
                "workflow_id": str(workflow_id),
                "steps": step_results,
                "error": "update_workflow_status failed",
            }
        step_results.append({"step": "update_workflow_status", "ok": True, "result": status_result})

        estimated_minutes = max(1, len(plan) * _ESTIMATED_MINUTES_PER_STEP)
        broadcast = (
            "【调度官播报】本群已开跑。"
            f"共 {len(plan)} 步骤预计 {estimated_minutes} 分钟。"
            "执行位请按 DAG 顺序等待调度分发；过程产物会写入群文件夹 00-03 目录。"
        )
        try:
            message_result = await send_channel_message(str(group_id), broadcast)
            step_results.append({"step": "send_channel_message", "ok": True, "result": message_result})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[SchedulerKickoff] send_channel_message failed for {}", workflow_id)
            step_results.append({"step": "send_channel_message", "ok": False, "error": str(exc)})
            return {
                "ok": False,
                "workflow_id": str(workflow_id),
                "group_id": str(group_id),
                "scheduler_agent_id": str(leader_agent_id),
                "steps": step_results,
                "error": "send_channel_message failed",
            }

    try:
        await run_repository.mark_run_started(db, workflow_id=workflow_id)
    except Exception as exc:  # noqa: BLE001 — DB failure should not block kickoff summary
        logger.exception("[SchedulerKickoff] mark_run_started failed for {}", workflow_id)
        step_results.append({"step": "mark_run_started", "ok": False, "error": str(exc)})
        return {
            "ok": False,
            "workflow_id": str(workflow_id),
            "group_id": str(group_id),
            "scheduler_agent_id": str(leader_agent_id),
            "steps_count": len(plan),
            "estimated_minutes": estimated_minutes,
            "steps": step_results,
            "error": "mark_run_started failed",
        }
    step_results.append({"step": "mark_run_started", "ok": True, "result": {"status": "active"}})

    return {
        "ok": True,
        "workflow_id": str(workflow_id),
        "group_id": str(group_id),
        "scheduler_agent_id": str(leader_agent_id),
        "steps_count": len(plan),
        "estimated_minutes": estimated_minutes,
        "steps": step_results,
    }


__all__ = ["run_scheduler_kickoff"]
