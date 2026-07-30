"""Bridge group-workflow lifecycle events into OKR progress collection."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.group import Group, GroupMember
from app.models.group_workflow import GroupWorkflow, GroupWorkflowItem, GroupWorkflowStage
from app.models.okr import OKRSettings
from app.models.participant import Participant
from app.services import okr_daily_collection
from app.services.okr_settings_helpers import (
    KNOWN_WORKFLOW_EVENTS,
    normalize_excluded_group_ids,
    normalize_workflow_events,
    workflow_push_active,
)

logger = logging.getLogger(__name__)


def _truncate(text: str, *, limit: int = 280) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


async def build_stage_prefill(
    db: AsyncSession,
    *,
    group: Group,
    workflow: GroupWorkflow,
    stage: GroupWorkflowStage | None,
    event_key: str,
) -> str:
    lines = [
        f"【项目进度节点 · {event_key}】",
        f"群：{group.name}",
        f"工作流：{workflow.name}",
    ]
    if stage is not None:
        lines.append(f"阶段：{stage.title}")
        if stage.goal:
            lines.append(f"阶段目标：{_truncate(stage.goal, limit=200)}")
        items = list(
            (
                await db.execute(
                    select(GroupWorkflowItem)
                    .where(GroupWorkflowItem.stage_id == stage.id)
                    .order_by(GroupWorkflowItem.updated_at.desc())
                    .limit(8)
                )
            ).scalars().all()
        )
        done = [item for item in items if item.status == "done"]
        blocked = [item for item in items if item.status == "blocked"]
        if done:
            lines.append(
                "已完成："
                + "；".join(_truncate(item.title, limit=40) for item in done[:5])
            )
        if blocked:
            lines.append(
                "阻塞："
                + "；".join(
                    _truncate(f"{item.title}（{item.blocked_reason or '无原因'}）", limit=60)
                    for item in blocked[:3]
                )
            )
        evidence_bits: list[str] = []
        for item in done:
            for entry in (item.evidence or [])[-2:]:
                if isinstance(entry, dict) or entry:
                    evidence_bits.append(_truncate(str(entry), limit=80))
            if len(evidence_bits) >= 4:
                break
        if evidence_bits:
            lines.append("证据摘要：" + " | ".join(evidence_bits[:4]))
    lines.append("请基于以上项目进度补充你的 OKR 进展、风险与下一步。")
    return "\n".join(lines)


async def on_workflow_event(
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    event_key: str,
    workflow_id: uuid.UUID,
    stage_id: uuid.UUID | None = None,
) -> dict | None:
    """Best-effort OKR collection kickoff for a workflow lifecycle event.

    Opens its own DB sessions so callers can safely ignore failures.
    """
    if event_key not in KNOWN_WORKFLOW_EVENTS:
        return None
    try:
        async with async_session() as db:
            settings = await db.scalar(select(OKRSettings).where(OKRSettings.tenant_id == tenant_id))
            if settings is None or not workflow_push_active(settings):
                return None
            events = normalize_workflow_events(settings.workflow_trigger_events)
            if event_key not in events:
                return None
            excluded = set(normalize_excluded_group_ids(settings.excluded_group_ids))
            if str(group_id) in excluded:
                return None
            group = await db.scalar(
                select(Group).where(
                    Group.id == group_id,
                    Group.tenant_id == tenant_id,
                    Group.deleted_at.is_(None),
                )
            )
            workflow = await db.scalar(
                select(GroupWorkflow).where(
                    GroupWorkflow.id == workflow_id,
                    GroupWorkflow.tenant_id == tenant_id,
                    GroupWorkflow.group_id == group_id,
                )
            )
            if group is None or workflow is None:
                return None
            stage = None
            if stage_id is not None:
                stage = await db.scalar(select(GroupWorkflowStage).where(GroupWorkflowStage.id == stage_id))
            prefill = await build_stage_prefill(
                db, group=group, workflow=workflow, stage=stage, event_key=event_key
            )
            member_rows = list(
                (
                    await db.execute(
                        select(Participant)
                        .join(GroupMember, GroupMember.participant_id == Participant.id)
                        .where(GroupMember.group_id == group_id)
                    )
                ).scalars().all()
            )
            await db.commit()

        return await okr_daily_collection.trigger_workflow_collection_for_group(
            tenant_id=tenant_id,
            group_id=group_id,
            participants=member_rows,
            prefill=prefill,
            event_key=event_key,
            report_day=datetime.now(UTC).date(),
        )
    except Exception:
        logger.exception(
            "OKR workflow bridge failed for group=%s event=%s", group_id, event_key
        )
        return None


async def notify_workflow_event(
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    event_key: str,
    workflow_id: uuid.UUID,
    stage_id: uuid.UUID | None = None,
) -> None:
    """Swallow-all wrapper for group_workflow service hooks."""
    await on_workflow_event(
        tenant_id=tenant_id,
        group_id=group_id,
        event_key=event_key,
        workflow_id=workflow_id,
        stage_id=stage_id,
    )


__all__ = [
    "KNOWN_WORKFLOW_EVENTS",
    "build_stage_prefill",
    "notify_workflow_event",
    "on_workflow_event",
]
