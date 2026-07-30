"""Classify failed group Runs and notify the group leader (no auto-resume)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.group_run_resume import GroupRunResumeJob
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.services import group_message_service
from app.services.llm.client import LLMMessage
from app.services.llm.single_step import complete_llm_once

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL_SECONDS = 1800
DEFAULT_EXPIRE_HOURS = 24
_MODEL_QUOTA_MARKERS = (
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "insufficient",
    "billing",
    "provider",
    "model_call_failed",
    "429",
)


def classify_failure(*, error_code: str, error_summary: str) -> str:
    code = (error_code or "").strip().lower()
    summary = (error_summary or "").strip().lower()
    if code == "model_call_failed":
        return "model_quota"
    blob = f"{code} {summary}"
    if any(marker in blob for marker in _MODEL_QUOTA_MARKERS):
        return "model_quota"
    return "general"


def _truncate(text: str, limit: int = 1200) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def error_from_lifecycle(lifecycle: Mapping[str, Any]) -> tuple[str, str]:
    error = lifecycle.get("error")
    code = ""
    message = ""
    if isinstance(error, Mapping):
        raw_code = error.get("code")
        if isinstance(raw_code, str):
            code = raw_code.strip()
        raw_message = error.get("message") or error.get("detail") or error.get("reason")
        if isinstance(raw_message, str):
            message = raw_message.strip()
    if not message:
        reason = lifecycle.get("reason")
        if isinstance(reason, str):
            message = reason.strip()
    return code or "run_failed", _truncate(message or code or "run_failed")


def build_notify_content(
    *,
    kind: str,
    phase: str,
    run_id: uuid.UUID,
    error_code: str,
    error_summary: str,
    agent_name: str | None,
) -> str:
    agent_part = f"；失败执行者：{agent_name}" if agent_name else ""
    base = (
        f"群任务失败通知：Run ID={run_id}；错误码={error_code or 'unknown'}；"
        f"摘要={error_summary or '无'}{agent_part}。"
        "请你决定是否重试；系统不会自动续跑该 Run。"
    )
    if kind == "model_quota" and phase == "initial":
        return (
            f"{base} 判定为模型调用/额度类失败，已安排每 30 分钟自动确认模型是否恢复；"
            "请暂缓盲目重试，或在恢复通知后再决定。"
        )
    if kind == "model_quota" and phase == "recovered":
        return (
            f"{base} 探测显示模型已可调用。请决定是否重试 Run {run_id}；"
            "系统不会自动续跑。"
        )
    if kind == "model_quota" and phase == "timed_out":
        return (
            f"{base} 24 小时内模型探测未恢复，已停止自动确认。"
            f"请人工检查模型配置/额度后决定是否重试 Run {run_id}。"
        )
    return base


async def _resolve_group_scope(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> tuple[ChatSession, Group] | None:
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.deleted_at.is_(None),
        )
    )
    if session is None or session.session_type != "group" or session.group_id is None:
        return None
    group = await db.scalar(
        select(Group).where(
            Group.id == session.group_id,
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
        )
    )
    if group is None or group.leader_participant_id is None:
        return None
    return session, group


async def _agent_participant_id(db: AsyncSession, *, agent_id: str | None) -> uuid.UUID | None:
    if not agent_id:
        return None
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        return None
    return await db.scalar(
        select(Participant.id).where(Participant.type == "agent", Participant.ref_id == agent_uuid)
    )


async def notify_leader(
    db: AsyncSession,
    *,
    job: GroupRunResumeJob,
    group: Group,
    phase: str,
    agent_name: str | None = None,
) -> None:
    leader_id = group.leader_participant_id
    if leader_id is None:
        return
    content = build_notify_content(
        kind=job.kind,
        phase=phase,
        run_id=job.failed_run_id,
        error_code=job.error_code,
        error_summary=job.error_summary,
        agent_name=agent_name,
    )
    message_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"group-run-resume:{job.id}:{phase}",
    )
    await group_message_service.enqueue_group_message(
        db,
        tenant_id=job.tenant_id,
        group_id=job.group_id,
        session_id=job.session_id,
        sender_participant_id=leader_id,
        mention_participant_ids=[leader_id],
        message_id=message_id,
        content=content,
    )
    job.leader_notified_at = datetime.now(UTC)


async def ensure_resume_job_for_failed_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    session_id: uuid.UUID | None,
    agent_id: str | None,
    error_code: str,
    error_summary: str,
) -> GroupRunResumeJob | None:
    """Create or reuse one resume job and notify the leader when appropriate."""
    if session_id is None:
        return None
    try:
        session_uuid = uuid.UUID(str(session_id))
    except ValueError:
        return None
    scope = await _resolve_group_scope(db, tenant_id=tenant_id, session_id=session_uuid)
    if scope is None:
        return None
    session, group = scope
    kind = classify_failure(error_code=error_code, error_summary=error_summary)
    now = datetime.now(UTC)
    failed_agent_participant_id = await _agent_participant_id(db, agent_id=agent_id)
    stmt = (
        insert(GroupRunResumeJob)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            group_id=group.id,
            session_id=session.id,
            failed_run_id=run_id,
            failed_agent_participant_id=failed_agent_participant_id,
            error_code=_truncate(error_code, 120),
            error_summary=_truncate(error_summary),
            kind=kind,
            status="pending",
            next_check_at=now + timedelta(seconds=DEFAULT_CHECK_INTERVAL_SECONDS)
            if kind == "model_quota"
            else None,
            check_interval_seconds=DEFAULT_CHECK_INTERVAL_SECONDS,
            expires_at=now + timedelta(hours=DEFAULT_EXPIRE_HOURS) if kind == "model_quota" else None,
            check_count=0,
        )
        .on_conflict_do_nothing(index_elements=["failed_run_id"])
        .returning(GroupRunResumeJob.id)
    )
    inserted_id = await db.scalar(stmt)
    job = await db.scalar(
        select(GroupRunResumeJob).where(GroupRunResumeJob.failed_run_id == run_id).with_for_update()
    )
    if job is None:
        return None
    if inserted_id is None and job.leader_notified_at is not None:
        return job

    agent_name = None
    if job.failed_agent_participant_id is not None:
        agent = await db.scalar(
            select(Participant).where(Participant.id == job.failed_agent_participant_id)
        )
        if agent is not None:
            agent_name = agent.display_name

    if job.kind == "general":
        if job.status == "notified" and job.leader_notified_at is not None:
            return job
        await notify_leader(db, job=job, group=group, phase="general", agent_name=agent_name)
        job.status = "notified"
        await db.flush()
        return job

    if job.leader_notified_at is None:
        await notify_leader(db, job=job, group=group, phase="initial", agent_name=agent_name)
        await db.flush()
    return job


async def probe_model_available(db: AsyncSession, *, model_id: str | None) -> bool:
    """Minimal one-shot probe; never starts a product Run."""
    if not model_id:
        return False
    try:
        model_uuid = uuid.UUID(model_id)
    except ValueError:
        return False
    model = await db.scalar(select(LLMModel).where(LLMModel.id == model_uuid))
    if model is None or not getattr(model, "is_active", True):
        return False
    try:
        await complete_llm_once(
            model,
            [LLMMessage(role="user", content="Reply with OK only.")],
            tools=None,
            agent_id=None,
            supports_vision=False,
        )
        return True
    except Exception as exc:
        logger.info("Model probe failed for %s: %s", model_id, exc)
        return False


async def process_due_resume_jobs_once(*, limit: int = 20) -> int:
    """Scan pending model_quota jobs that are due; notify on recover/timeout. No auto-resume."""
    from app.database import async_session
    from app.models.agent_run import AgentRun

    processed = 0
    async with async_session() as db, db.begin():
        now = datetime.now(UTC)
        rows = list(
            (
                await db.execute(
                    select(GroupRunResumeJob)
                    .where(
                        GroupRunResumeJob.kind == "model_quota",
                        GroupRunResumeJob.status == "pending",
                        GroupRunResumeJob.next_check_at.is_not(None),
                        GroupRunResumeJob.next_check_at <= now,
                    )
                    .order_by(GroupRunResumeJob.next_check_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for job in rows:
            group = await db.scalar(select(Group).where(Group.id == job.group_id))
            if group is None or group.leader_participant_id is None:
                job.status = "cancelled"
                processed += 1
                continue
            agent_name = None
            if job.failed_agent_participant_id is not None:
                agent = await db.scalar(
                    select(Participant).where(Participant.id == job.failed_agent_participant_id)
                )
                if agent is not None:
                    agent_name = agent.display_name

            job.last_checked_at = now
            job.check_count = int(job.check_count or 0) + 1
            expires_at = job.expires_at
            if expires_at is not None and now >= expires_at:
                await notify_leader(db, job=job, group=group, phase="timed_out", agent_name=agent_name)
                job.status = "timed_out"
                job.next_check_at = None
                processed += 1
                continue

            run = await db.scalar(select(AgentRun).where(AgentRun.id == job.failed_run_id))
            model_id = str(run.model_id) if run is not None and run.model_id is not None else None
            available = await probe_model_available(db, model_id=model_id)
            if available:
                await notify_leader(db, job=job, group=group, phase="recovered", agent_name=agent_name)
                job.status = "recovered_notified"
                job.next_check_at = None
            else:
                interval = job.check_interval_seconds or DEFAULT_CHECK_INTERVAL_SECONDS
                job.next_check_at = now + timedelta(seconds=interval)
            processed += 1
    return processed


__all__ = [
    "classify_failure",
    "ensure_resume_job_for_failed_run",
    "process_due_resume_jobs_once",
    "build_notify_content",
    "error_from_lifecycle",
]
