"""FastAPI router for the Intent-Driven Project Orchestrator.

Endpoints:
  POST /api/orchestrator/drafts            — propose a draft from user message
  GET  /api/orchestrator/drafts            — list drafts for current user
  GET  /api/orchestrator/drafts/{id}       — get a specific draft
  POST /api/orchestrator/drafts/{id}/approve — mark draft approved
  POST /api/orchestrator/drafts/{id}/create  — AtomicCreator: materialize draft

Note: This router is NOT auto-mounted in main.py. The operator must wire
it explicitly (see docs/orchestrator-deployment.md). Keeping it optional
preserves zero-config stability of the existing backend.
"""
from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.services.orchestrator.draft_schemas import Draft, ProjectCreated
from app.services.orchestrator.orchestrator_service import OrchestratorService
from app.services.orchestrator.atomic_creator import AtomicCreator, AtomicCreatorError

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


def _get_orchestrator_service() -> OrchestratorService:
    """Dependency: provide the OrchestratorService.

    Returns a stub if not configured. Real wiring depends on the host app
    (the operator must build IntentAnalyzer / AgentSelector / GroupPlanner
    with real LLM + template backends).
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="OrchestratorService is not configured. Wire it in main.py.",
    )


@router.post("/drafts", response_model=Draft, status_code=status.HTTP_201_CREATED)
async def propose_draft(
    payload: dict,
    service: OrchestratorService = Depends(_get_orchestrator_service),
) -> Draft:
    user_message = payload.get("user_message")
    if not user_message or not isinstance(user_message, str):
        raise HTTPException(status_code=400, detail="user_message is required")
    tenant_id = payload.get("tenant_id")  # in real wiring: from auth context
    user_id = payload.get("user_id")
    if not tenant_id or not user_id:
        raise HTTPException(status_code=400, detail="tenant_id and user_id required")
    return await service.propose(
        tenant_id=uuid.UUID(tenant_id),
        user_id=uuid.UUID(user_id),
        user_message=user_message,
    )


@router.post("/drafts/{draft_id}/approve", response_model=Draft)
async def approve_draft(
    draft_id: uuid.UUID,
    payload: dict,
    service: OrchestratorService = Depends(_get_orchestrator_service),
) -> Draft:
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    try:
        return await service.approve(tenant_id=uuid.UUID(tenant_id), draft_id=draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/drafts/{draft_id}/create", response_model=ProjectCreated)
async def create_draft(
    draft_id: uuid.UUID,
    payload: dict,
    creator: AtomicCreator = Depends(lambda: AtomicCreator()),
):
    """AtomicCreator: materialize the Draft into Group + Agents + OKR + Tasks.

    In production this dep is wired with real services (groups / agents / okr /
    task_board / runtime_command_intake). The default no-op deps create the
    Draft idempotently but skip side effects.
    """
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    try:
        result = await creator.create(tenant_id=uuid.UUID(tenant_id), draft_id=draft_id)
        return ProjectCreated(**result)
    except AtomicCreatorError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
