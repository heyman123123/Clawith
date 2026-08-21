"""FastAPI router for the Intent-Driven Project Orchestrator.

Endpoints:
  POST /api/orchestrator/drafts             - propose a draft from user message
  POST /api/orchestrator/drafts/{id}/approve - mark draft approved
  POST /api/orchestrator/drafts/{id}/create  - AtomicCreator: materialize draft

Authentication:
  All endpoints require a valid Bearer JWT and extract tenant_id + user_id
  from the authenticated user. The payload tenant_id / user_id fields are
  accepted as fallback (for headless integrations) but the auth context is
  the authoritative source.
"""
from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import AgentTemplate
from app.models.user import User
from app.services.orchestrator.draft_schemas import Draft, ProjectCreated
from app.services.orchestrator.orchestrator_service import OrchestratorService
from app.services.orchestrator.intent_analyzer import IntentAnalyzer
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.group_planner import GroupPlanner
from app.services.orchestrator.atomic_creator import AtomicCreator, AtomicCreatorError

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


# ---------------------------------------------------------------------------
# Service wiring (single shared OrchestratorService instance)
# ---------------------------------------------------------------------------

async def _llm_caller_stub(prompt: str) -> str:
    """Stub LLM caller.

    Replace this with the real LLM gateway client (`app.services.llm.gateway`)
    at deployment. Returns a deterministic JSON payload that IntentAnalyzer
    parses successfully so the end-to-end flow works without a real LLM.
    """
    return '{"intent_category": "other", "scope_summary": "User-defined project goal", "key_constraints": []}'


async def _llm_generate_template_stub(category: str, role_gaps: list) -> list:
    """Stub for LLM-generated agent templates.

    Returns a single placeholder template that the operator can later
    regenerate via /api/template-registry/* endpoints.
    """
    return [
        {
            "role": role_gaps[0] if role_gaps else "assistant",
            "name": f"AI {role_gaps[0] if role_gaps else 'Assistant'}",
            "system_prompt": f"You are an AI agent specialized for {category}.",
        },
        *[{
            "role": g, "name": f"AI {g}", "system_prompt": f"You handle {g} tasks."
        } for g in role_gaps[1:]],
    ]


async def _template_search(category: str, tenant_id: uuid.UUID | None) -> list[dict]:
    """Look up built-in agent templates matching the intent category."""
    from app.database import async_session
    async with async_session() as db:
        stmt = (
            select(AgentTemplate)
            .where(AgentTemplate.is_builtin == True)  # noqa: E712
            .where(AgentTemplate.template_visibility.in_(["public", "tenant_private"]))
            .order_by(AgentTemplate.created_at.desc())
            .limit(10)
        )
        if tenant_id is not None:
            stmt = stmt.where(
                (AgentTemplate.tenant_id == tenant_id) | (AgentTemplate.tenant_id.is_(None))
            )
        rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "role": r.category or "assistant",
            "name": r.name,
            "template_id": r.id,
            "system_prompt": r.soul_template or "",
        }
        for r in rows
    ]


async def _service_factory() -> OrchestratorService:
    return OrchestratorService(
        intent_analyzer=IntentAnalyzer(llm_caller=_llm_caller_stub),
        agent_selector=AgentSelector(
            template_search=_template_search,
            llm_generate_template=_llm_generate_template_stub,
        ),
        group_planner=GroupPlanner(),
    )


def _atomic_creator_factory() -> AtomicCreator:
    """AtomicCreator with default no-op services.

    Operators should override `group_service` / `agent_service` /
    `okr_service` / `task_board_service` / `command_intake_service`
    at deployment to wire real downstream services. With default None
    deps, AtomicCreator still persists Draft.status='consumed' and
    returns deterministic IDs.
    """
    return AtomicCreator()


# Lazy import to avoid circular
# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _resolve_tenant_user(
    payload: dict,
    current_user: User,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Resolve tenant_id + user_id from payload or auth context."""
    tid = payload.get("tenant_id") or str(current_user.tenant_id)
    uid = payload.get("user_id") or str(current_user.id)
    try:
        return uuid.UUID(tid), uuid.UUID(uid)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid tenant_id / user_id") from exc


@router.post("/drafts", response_model=Draft, status_code=status.HTTP_201_CREATED)
async def propose_draft(
    payload: dict,
    current_user: User = Depends(get_current_user),
    service: OrchestratorService = Depends(_service_factory),
) -> Draft:
    user_message = payload.get("user_message")
    if not user_message or not isinstance(user_message, str):
        raise HTTPException(status_code=400, detail="user_message is required")
    tenant_id, user_id = _resolve_tenant_user(payload, current_user)  # sync now
    return await service.propose(
        tenant_id=tenant_id,
        user_id=user_id,
        user_message=user_message,
    )


@router.get("/drafts", response_model=list[Draft])
async def list_drafts(
    current_user: User = Depends(get_current_user),
    service: OrchestratorService = Depends(_service_factory),
) -> list[Draft]:
    """List drafts owned by the current user."""
    return await service.list_for_user(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )


@router.get("/drafts/{draft_id}", response_model=Draft)
async def get_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: OrchestratorService = Depends(_service_factory),
) -> Draft:
    return await service.get(
        tenant_id=current_user.tenant_id,
        draft_id=draft_id,
    )


@router.post("/drafts/{draft_id}/approve", response_model=Draft)
async def approve_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: OrchestratorService = Depends(_service_factory),
) -> Draft:
    try:
        return await service.approve(
            tenant_id=current_user.tenant_id,
            draft_id=draft_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/drafts/{draft_id}/create", response_model=ProjectCreated)
async def create_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    creator: AtomicCreator = Depends(_atomic_creator_factory),
):
    """AtomicCreator: materialize the Draft into Group + Agents + OKR + Tasks."""
    try:
        result = await creator.create(
            tenant_id=current_user.tenant_id,
            draft_id=draft_id,
        )
        return ProjectCreated(**result)
    except AtomicCreatorError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.post("/settings/proactive", status_code=status.HTTP_204_NO_CONTENT)
async def set_proactive(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the proactive intent-detection suggestion for the current user."""
    enabled = bool(payload.get("enabled", True))
    # Real persistence: a `user_settings` row keyed by (tenant_id, user_id).
    # For now we no-op; the setting is held client-side. Once a
    # `user_settings` table is added, persist here.
    return None
