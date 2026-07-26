"""HTTP boundary for AgentRun user-facing operations (retry)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.agent_runtime.retry_run import (
    RetryRunError,
    create_retry_run,
    load_retryable_run,
)


router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"])


class RetryRunIn(BaseModel):
    strategy: str = Field(default="fresh_context")


class RetryRunOut(BaseModel):
    run_id: uuid.UUID
    thread_id: str
    command_id: uuid.UUID
    runtime_type: str
    created: bool
    retry_of_run_id: uuid.UUID
    strategy: str


@router.post("/{run_id}/retry", response_model=RetryRunOut)
async def retry_agent_run(
    run_id: uuid.UUID,
    body: RetryRunIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetryRunOut:
    if current_user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User has no tenant")
    source = await load_retryable_run(
        db,
        tenant_id=current_user.tenant_id,
        run_id=run_id,
    )
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    try:
        result = await create_retry_run(
            db,
            source_run=source,
            user=current_user,
            strategy=body.strategy,
        )
    except RetryRunError as exc:
        status_code = status.HTTP_400_BAD_REQUEST
        if exc.code == "retry_strategy_not_implemented":
            status_code = status.HTTP_501_NOT_IMPLEMENTED
        elif exc.code in {"retry_forbidden", "session_unavailable"}:
            status_code = status.HTTP_403_FORBIDDEN
        raise HTTPException(status_code=status_code, detail=exc.message) from exc

    await db.commit()
    return RetryRunOut(
        run_id=result.run_id,
        thread_id=result.thread_id,
        command_id=result.command_id,
        runtime_type=result.runtime_type,
        created=result.created,
        retry_of_run_id=result.retry_of_run_id,
        strategy=result.strategy,
    )
