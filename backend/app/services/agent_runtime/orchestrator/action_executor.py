"""ActionExecutor: executes ActionPlan via TaskBoardService + RuntimeCommandIntake.

C1 compliance: Does NOT call LangGraph nodes directly. For executing agents,
it enqueues commands through RuntimeCommandIntake; for task board mutations,
it uses TaskBoardService.

Each action execution is independently try/excepted so a single failure
does not abort the whole plan.
"""
from __future__ import annotations
import uuid
import logging
from typing import Any
from app.services.agent_runtime.orchestrator.checkpoint_state import ActionPlan

logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(
        self,
        *,
        task_board_service=None,
        command_intake_service=None,
    ) -> None:
        self.task_board_service = task_board_service
        self.command_intake_service = command_intake_service

    async def execute(
        self,
        *,
        plan: ActionPlan,
        group_id: uuid.UUID,
        tenant_id: uuid.UUID,
        chief_agent_id: uuid.UUID,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {
            "review_artifact_paths": plan.review_artifact_paths,
            "moves_attempted": 0,
            "moves_succeeded": 0,
            "moves_failed": 0,
            "dispatches_attempted": 0,
            "dispatches_succeeded": 0,
            "dispatches_failed": 0,
            "notify_user": plan.notify_user,
        }
        # 1. Move cards
        for move in plan.move_cards:
            results["moves_attempted"] += 1
            try:
                if self.task_board_service is not None:
                    from app.services.task_board.column_defs import TaskColumn
                    await self.task_board_service.move_card(
                        tenant_id=tenant_id,
                        card_id=move.card_id,
                        to_column=TaskColumn(move.to_column),
                        actor_id=chief_agent_id,
                        actor_type="chief",
                        expected_version=move.expected_version,
                    )
                results["moves_succeeded"] += 1
            except Exception as exc:
                logger.warning("Move failed for card %s: %s", move.card_id, exc)
                results["moves_failed"] += 1

        # 2. Assign cards
        for assign in plan.assign_cards:
            results["moves_attempted"] += 1
            try:
                if self.task_board_service is not None:
                    await self.task_board_service.assign_card(
                        tenant_id=tenant_id,
                        card_id=assign.card_id,
                        assignee_agent_id=assign.assignee_agent_id,
                        actor_id=chief_agent_id,
                        actor_type="chief",
                        expected_version=assign.expected_version,
                    )
                results["moves_succeeded"] += 1
            except Exception as exc:
                logger.warning("Assign failed for card %s: %s", assign.card_id, exc)
                results["moves_failed"] += 1

        # 3. Dispatch tasks (via RuntimeCommandIntake, C1)
        for task in plan.dispatch_tasks:
            results["dispatches_attempted"] += 1
            try:
                if self.command_intake_service is not None:
                    await self.command_intake_service.enqueue_agent_run(
                        tenant_id=tenant_id,
                        group_id=group_id,
                        agent_role=task.agent_role,
                        title=task.title,
                        description=task.description,
                    )
                results["dispatches_succeeded"] += 1
            except Exception as exc:
                logger.warning("Dispatch failed for %s: %s", task.agent_role, exc)
                results["dispatches_failed"] += 1

        return results
