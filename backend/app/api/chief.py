"""FastAPI router for Chief Runtime 1:1 chat.

Endpoints:
  GET  /api/chief/{chief_run_id}/messages  - list messages (oldest first)
  POST /api/chief/{chief_run_id}/messages  - append user message (+ Chief reply)
  GET  /api/chief/{chief_run_id}/status     - run lifecycle (active/degraded/...)

Chief replies are echoed from a simple heuristic until the real Chief
Runtime is wired into CommandWorker dispatch. The echo is non-trivial:
it acknowledges the user's message and includes a deterministic summary
of recent activity in the project (task counts, agent count) so the UI
shows something useful while the real Chief is being deployed.
"""
from __future__ import annotations
import logging
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func

from app.core.security import get_current_user
from app.database import async_session
from app.models.chief_run import ChiefRun
from app.models.chief_message import ChiefMessage
from app.models.task_card import TaskCard
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chief", tags=["chief"])


async def _ensure_chief_run(tenant_id: uuid.UUID, chief_run_id: uuid.UUID) -> ChiefRun:
    async with async_session() as db:
        run = (await db.execute(
            select(ChiefRun).where(
                ChiefRun.id == chief_run_id,
                ChiefRun.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Chief run not found")
        return run


@router.get("/{chief_run_id}/status")
async def get_status(
    chief_run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    run = await _ensure_chief_run(current_user.tenant_id, chief_run_id)
    return {
        "id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "group_id": str(run.group_id),
        "chief_agent_id": str(run.chief_agent_id),
        "status": run.status,
        "failure_count": run.failure_count,
        "last_failure_reason": run.last_failure_reason,
    }


@router.get("/{chief_run_id}/messages")
async def list_messages(
    chief_run_id: uuid.UUID,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
):
    run = await _ensure_chief_run(current_user.tenant_id, chief_run_id)
    async with async_session() as db:
        rows = (await db.execute(
            select(ChiefMessage)
            .where(ChiefMessage.chief_run_id == chief_run_id)
            .order_by(ChiefMessage.created_at.asc())
            .limit(limit)
        )).scalars().all()
    return [
        {
            "id": str(r.id),
            "from": r.role if r.role == "user" else "chief",
            "content": r.content,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/{chief_run_id}/messages", status_code=201)
async def post_message(
    chief_run_id: uuid.UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    run = await _ensure_chief_run(current_user.tenant_id, chief_run_id)
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    # 1. Persist the user message
    user_msg_id = uuid.uuid4()
    async with async_session() as db:
        db.add(ChiefMessage(
            id=user_msg_id,
            tenant_id=current_user.tenant_id,
            chief_run_id=chief_run_id,
            role="user",
            content=content,
            actor_id=current_user.id,
        ))
        # 2. Generate Chief reply (deterministic stub until real Chief Runtime is wired)
        #    Provide useful context: project activity snapshot
        task_count = (await db.execute(
            select(func.count(TaskCard.id)).where(TaskCard.group_id == run.group_id)
        )).scalar() or 0
        done_count = (await db.execute(
            select(func.count(TaskCard.id)).where(
                TaskCard.group_id == run.group_id,
                TaskCard.column == "done",
            )
        )).scalar() or 0
        chief_reply = (
            f"收到。已记录你的指令。\n"
            f"项目当前状态:{task_count} 个任务,其中 {done_count} 个已完成。\n"
            f"我会持续监听任务进展,有需要时主动调整优先级。\n"
            f"(完整 Chief Runtime 待部署,当前为 stub 回复)"
        )
        chief_msg_id = uuid.uuid4()
        db.add(ChiefMessage(
            id=chief_msg_id,
            tenant_id=current_user.tenant_id,
            chief_run_id=chief_run_id,
            role="chief",
            content=chief_reply,
            actor_id=run.chief_agent_id,
        ))
        await db.commit()

    return {
        "id": str(user_msg_id),
        "content": content,
        "chief_reply": {
            "id": str(chief_msg_id),
            "content": chief_reply,
        },
    }
