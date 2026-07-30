"""Enqueue once-per-day workflow stats digests for group leaders.

Digests are confirmation-only: they must not drive stage advancement.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.database import async_session
from app.models.group_workflow import GroupWorkflow, GroupWorkflowEvent, GroupWorkflowItem, GroupWorkflowStage
from app.services.group_workflow.service import _event, _human_confirm_targets

logger = logging.getLogger(__name__)


def _utc_day_key(now: datetime | None = None) -> str:
    stamp = now or datetime.now(UTC)
    return stamp.astimezone(UTC).date().isoformat()


async def _stage_counts(db, workflow_id) -> dict[str, int]:
    rows = await db.execute(
        select(GroupWorkflowStage.status, func.count())
        .where(GroupWorkflowStage.workflow_id == workflow_id)
        .group_by(GroupWorkflowStage.status)
    )
    return {str(status): int(count) for status, count in rows.all()}


async def _item_counts(db, workflow_id) -> dict[str, int]:
    rows = await db.execute(
        select(GroupWorkflowItem.status, func.count())
        .where(GroupWorkflowItem.workflow_id == workflow_id)
        .group_by(GroupWorkflowItem.status)
    )
    return {str(status): int(count) for status, count in rows.all()}


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "、".join(f"{status} {count}" for status, count in sorted(counts.items()))


async def enqueue_daily_digests_once(*, now: datetime | None = None) -> int:
    """Create pending leader_action digests for active workflows missing today's key."""
    day_key = _utc_day_key(now)
    created = 0
    async with async_session() as db, db.begin():
        workflows = list(
            (
                await db.execute(
                    select(GroupWorkflow).where(
                        GroupWorkflow.status.in_(("active", "awaiting_approval")),
                        GroupWorkflow.leader_participant_id.is_not(None),
                    )
                )
            ).scalars().all()
        )
        for workflow in workflows:
            idempotency_key = f"daily_digest:{day_key}"
            existing = await db.scalar(
                select(GroupWorkflowEvent.id).where(
                    GroupWorkflowEvent.workflow_id == workflow.id,
                    GroupWorkflowEvent.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                continue
            stage_counts = await _stage_counts(db, workflow.id)
            item_counts = await _item_counts(db, workflow.id)
            current_stage = None
            if workflow.current_stage_id is not None:
                current_stage = await db.scalar(
                    select(GroupWorkflowStage).where(GroupWorkflowStage.id == workflow.current_stage_id)
                )
            stage_title = current_stage.title if current_stage is not None else workflow.name
            summary = (
                f"工作流「{workflow.name}」UTC {day_key} 进度："
                f"阶段[{_format_counts(stage_counts)}]；"
                f"工作项[{_format_counts(item_counts)}]。"
            )
            await _event(
                db,
                workflow=workflow,
                event_type="leader_action",
                source="workflow",
                idempotency_key=idempotency_key,
                stage_id=workflow.current_stage_id,
                dispatch=True,
                payload={
                    "kind": "daily_digest",
                    "stage_title": stage_title,
                    "item_title": None,
                    "day": day_key,
                    "summary": summary,
                    "stage_counts": stage_counts,
                    "item_counts": item_counts,
                    "confirm_targets": await _human_confirm_targets(db, group_id=workflow.group_id),
                },
            )
            created += 1
    if created:
        logger.info("Enqueued %s group workflow daily digests for %s", created, day_key)
    return created


__all__ = ["enqueue_daily_digests_once"]
