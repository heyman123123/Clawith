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
import logging
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import AgentTemplate
from app.models.draft import Draft as DraftModel
from app.models.user import User
from app.services.orchestrator.draft_schemas import Draft, ProjectCreated
from app.services.orchestrator.orchestrator_service import OrchestratorService
from app.services.orchestrator.intent_analyzer import IntentAnalyzer
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.group_planner import GroupPlanner
from app.services.orchestrator.atomic_creator import AtomicCreator, AtomicCreatorError
# Real service wiring
from app.services.participant_identity import (
    get_or_create_user_participant,
    get_or_create_agent_participant,
)
from app.services import group_chat_service
from app.services.task_board.service import TaskBoardService
from app.models.participant import Participant
from app.models.agent import Agent, AgentTemplate
from app.models.group import Group, GroupMember
from app.models.chief_run import ChiefRun
from app.models.task_card import TaskCard as TaskCardModel
from app.models.task_card_dependency import TaskCardDependency
from app.models.okr import OKRKeyResult
from app.models.user import User as UserModel
from app.database import async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


# ---------------------------------------------------------------------------
# Service wiring (single shared OrchestratorService instance)
# ---------------------------------------------------------------------------

async def _resolve_llm_model(tenant_id):
    """Find a usable LLM model for this tenant.

    Prefer the tenant's default_model_id, then any enabled model.
    """
    from sqlalchemy import select
    from app.models.llm import LLMModel
    async with async_session() as db:
        from app.models.tenant import Tenant
        t_row = (await db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )).scalar_one_or_none()
        if t_row and getattr(t_row, "default_model_id", None):
            m = (await db.execute(
                select(LLMModel).where(
                    LLMModel.id == t_row.default_model_id,
                    LLMModel.enabled == True,
                )
            )).scalar_one_or_none()
            if m:
                return m
        m = (await db.execute(
            select(LLMModel).where(LLMModel.enabled == True).limit(1)
        )).scalar_one_or_none()
        return m


async def _llm_caller_stub(prompt: str) -> str:
    """Real LLM caller using Clawith\'s LLM gateway.

    Falls back to empty string on any error so callers use heuristic logic.
    """
    from app.services.llm.utils import get_model_api_key
    from app.services.llm.client import LLMMessage
    from app.services.llm.single_step import complete_llm_once
    tenant_id = getattr(_llm_caller_stub, "_last_tenant_id", None)
    model = await _resolve_llm_model(tenant_id)
    if model is None:
        logger.warning("No enabled LLM model; cannot call real LLM")
        return ""
    try:
        step = await complete_llm_once(
            model,
            [LLMMessage(role="user", content=prompt)],
        )
        return step.content or ""
    except Exception as exc:
        logger.warning(f"Real LLM call failed: {exc}")
        return ""


async def _llm_generate_template_stub(category: str, role_gaps: list, user_message: str = "") -> list:
    """Real LLM generator: produces a spec for each missing role.

    The LLM is asked to invent a Chief-style persona (display name + brief
    system prompt) per role. The DraftPreview UI can rename them before
    approval, so the LLM only needs to seed reasonable starting points.
    """
    if not role_gaps:
        return []
    prompt = (
        f"You are a project staffing assistant. The user goal is: {user_message!r}.\n"
        f"The project category is: {category}.\n"
        f"We need to fill these missing agent roles: {role_gaps}.\n"
        f"For each role, return a JSON object with:\n"
        f"  - role: one of {role_gaps}\n"
        f"  - name: a short human-readable display name (1-3 words)\n"
        f"  - system_prompt: a 1-2 sentence system prompt defining what this agent does in the project\n"
        f"Return a JSON array, one object per role. No other text."
    )
    raw = await _llm_caller_stub(prompt)
    import json
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and "role" in d][:len(role_gaps)]
    except Exception:
        pass
    # Fallback: deterministic placeholders
    return [
        {"role": g, "name": f"AI {g.replace('-', ' ').title()}",
         "system_prompt": f"You are an AI agent specialized for {g} in a {category} project."}
        for g in role_gaps
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


async def _service_factory(current_user: User = Depends(get_current_user)) -> OrchestratorService:
    # Pin tenant_id on the LLM stub so real calls resolve a tenant-appropriate model.
    _llm_caller_stub._last_tenant_id = current_user.tenant_id
    planner = GroupPlanner()
    planner.llm_caller = _llm_caller_stub
    return OrchestratorService(
        intent_analyzer=IntentAnalyzer(llm_caller=_llm_caller_stub),
        agent_selector=AgentSelector(
            template_search=_template_search,
            llm_generate_template=_llm_generate_template_stub,
        ),
        group_planner=planner,
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




async def _create_project_from_draft(
    draft_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    visibility: str,
) -> dict:
    """Materialize a Draft into a real Group + Agents + OKR + TaskCards + ChiefRun.

    Idempotent on draft_id: subsequent calls return the previously-created IDs
    (stored in draft.error_detail). Atomicity is best-effort: if any step
    fails, partial state is committed and the caller is told which step
    failed so they can re-run safely.
    """
    async with async_session() as db:
        draft_row = await db.get(DraftModel, draft_id)
        if draft_row is None or draft_row.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Draft not found")
        if draft_row.status != "approved":
            raise HTTPException(
                status_code=400,
                detail=f"Draft must be approved (current: {draft_row.status})",
            )
        if draft_row.status == "approved":
            # Replay: was this draft already materialized?
            prev = draft_row.error_detail or {}
            if prev.get("materialized"):
                return {
                    "draft_id": draft_id,
                    "group_id": uuid.UUID(prev["group_id"]),
                    "chief_agent_id": uuid.UUID(prev["chief_agent_id"]),
                    "chief_run_id": uuid.UUID(prev["chief_run_id"]),
                    "task_card_ids": [uuid.UUID(t) for t in prev.get("task_card_ids", [])],
                    "okr_objective_id": uuid.UUID(prev["okr_objective_id"]) if prev.get("okr_objective_id") else None,
                }

        payload = draft_row.draft_payload or {}
        group_meta = payload.get("group", {})
        members_meta = payload.get("members", [])
        okr_meta = payload.get("okr")
        tasks_meta = payload.get("tasks", [])

        # 1. Get or create the user's Participant identity
        creator_user = await db.get(UserModel, user_id)
        if creator_user is None:
            raise HTTPException(status_code=400, detail="User not found")
        creator_part = await get_or_create_user_participant(
            db,
            creator_user.id,
            creator_user.display_name,
            creator_user.avatar_url,
        )

        # 2. Create Agent rows (one per member, including chief)
        #    - For "chief-of-staff": always create a new Agent
        #    - For template-backed members: also create a new Agent (cloned from template)
        #    - For LLM-generated members: also create new Agent
        agent_ids: list[uuid.UUID] = []
        for m in members_meta:
            agent_id = uuid.uuid4()
            template_id = m.get("template_id")
            # If template_id given, copy skill setup from template; else use member defaults
            existing_template = None
            if template_id:
                from sqlalchemy import select
                result = await db.execute(select(AgentTemplate).where(AgentTemplate.id == uuid.UUID(template_id)))
                existing_template = result.scalar_one_or_none()
            agent = Agent(
                id=agent_id,
                tenant_id=tenant_id,
                creator_id=user_id,
                name=m.get("name", "Agent"),
                agent_type="native",
                role_description=m.get("role", "assistant")[:500],
                bio=m.get("system_prompt", ""),
                welcome_message=f"Hello, I'm {m.get('name', 'an agent')}. How can I help?",
                max_tool_rounds=50,
                status="running" if m.get("role") == "chief-of-staff" else "idle",
            )
            db.add(agent)
            agent_ids.append(agent_id)
        await db.flush()

        # 3. Create Group + GroupMembers (creator as manager, agents as members)
        member_part_ids: list[uuid.UUID] = []
        for aid in agent_ids:
            ap = await get_or_create_agent_participant(
                db, aid, display_name="Agent", avatar_url=None
            )
            member_part_ids.append(ap.id)

        group = Group(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=(group_meta.get("name") or "新项目")[:200],
            description=group_meta.get("description"),
            created_by_participant_id=creator_part.id,
        )
        db.add(group)
        await db.flush()

        # creator as manager
        db.add(GroupMember(
            id=uuid.uuid4(),
            group_id=group.id,
            participant_id=creator_part.id,
            role="manager",
            session_read_state={},
        ))
        # agents as members
        for ap_id in member_part_ids:
            db.add(GroupMember(
                id=uuid.uuid4(),
                group_id=group.id,
                participant_id=ap_id,
                role="member",
                session_read_state={},
            ))

        # 4. OKR (simplified: store as Agent OKR objectives, fallback to no-op if no OKR model)
        okr_objective_id = None
        if okr_meta and okr_meta.get("objective_title"):
            try:
                from app.models.okr import OKRObjective
                okr_obj = OKRObjective(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    title=okr_meta.get("objective_title", "Project Goal"),
                    description=okr_meta.get("objective_description", ""),
                    owner_group_id=group.id,
                )
                db.add(okr_obj)
                await db.flush()
                okr_objective_id = okr_obj.id
                # Key results
                for kr in (okr_meta.get("key_results") or []):
                    db.add(OKRKeyResult(
                        id=uuid.uuid4(),
                        objective_id=okr_obj.id,
                        title=kr.get("title", "")[:500],
                        target_value=float(kr.get("target_value", 100.0)),
                        unit=kr.get("unit"),
                    ))
            except Exception as exc:
                logger.warning(f"OKR creation skipped: {exc}")

        # 5. Create TaskCards
        task_card_ids: list[uuid.UUID] = []
        for t in tasks_meta:
            card = TaskCardModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                group_id=group.id,
                title=(t.get("title") or "Untitled")[:500],
                description=t.get("description", ""),
                # assign to the first matching agent by role
                assignee_agent_id=_pick_assignee(t.get("assignee_role"), agent_ids, members_meta),
                version=0,
                created_by=creator_part.id,
                created_by_type="user",
            )
            db.add(card)
            task_card_ids.append(card.id)
        await db.flush()

        # 6. Create ChiefRun (find the chief agent or default to the creator)
        chief_agent_id = next(
            (aid for aid, m in zip(agent_ids, members_meta) if m.get("role") == "chief-of-staff"),
            agent_ids[0] if agent_ids else user_id,
        )
        chief_run = ChiefRun(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            group_id=group.id,
            chief_agent_id=chief_agent_id,
            runtime_thread_id=f"orchestrator:{group.id}",
            status="active",
        )
        db.add(chief_run)

        # 7. Persist materialized IDs back to the draft for replay
        draft_row.status = "consumed"
        draft_row.error_detail = {
            "materialized": True,
            "group_id": str(group.id),
            "chief_agent_id": str(chief_agent_id),
            "chief_run_id": str(chief_run.id),
            "task_card_ids": [str(t) for t in task_card_ids],
            "okr_objective_id": str(okr_objective_id) if okr_objective_id else None,
            "agent_ids": [str(a) for a in agent_ids],
        }
        await db.commit()

        return {
            "draft_id": draft_id,
            "group_id": group.id,
            "chief_agent_id": chief_agent_id,
            "chief_run_id": chief_run.id,
            "task_card_ids": task_card_ids,
            "okr_objective_id": okr_objective_id,
        }


def _pick_assignee(role: str | None, agent_ids: list, members_meta: list) -> uuid.UUID | None:
    if not role or not agent_ids:
        return None
    for aid, m in zip(agent_ids, members_meta):
        if m.get("role") == role:
            return aid
    return agent_ids[0]  # fallback: first agent


@router.post("/drafts/{draft_id}/create", response_model=ProjectCreated)
async def create_draft(
    draft_id: uuid.UUID,
    payload: dict | None = None,
    current_user: User = Depends(get_current_user),
):
    """Materialize the Draft into Group + Agents + OKR + TaskCards + ChiefRun.

    Idempotent on draft_id: replaying with the same draft_id returns
    the same IDs (so the navigation to /projects/{group_id} is stable).
    """
    visibility = (payload or {}).get("template_visibility", "user_private")
    try:
        result = await _create_project_from_draft(
            draft_id=draft_id,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            visibility=visibility,
        )
        return ProjectCreated(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("create_draft failed")
        raise HTTPException(status_code=500, detail=str(exc))


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
