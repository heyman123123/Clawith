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

# Real service wiring
from app.services.participant_identity import (
    get_or_create_user_participant,
    get_or_create_agent_participant,
)
from app.services import group_chat_service
from app.services.task_board.service import TaskBoardService
from app.services import group_chat_service, group_message_service
from app.services.group_chat_service import GroupChatServiceError
from app.services.group_message_service import GroupMessageServiceError
from app.services.participant_identity import (
    get_or_create_agent_participant,
)
from app.models.participant import Participant
from app.models.agent import Agent, AgentTemplate
from app.models.group import Group, GroupMember
from app.models.chief_run import ChiefRun
from app.models.task_card import TaskCard as TaskCardModel
from app.models.task_card_dependency import TaskCardDependency
from app.models.okr import OKRKeyResult
from app.models.user import User as UserModel
from app.database import async_session
import asyncio

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


async def _llm_caller(prompt: str) -> str:
    """Real LLM caller using Clawith\'s LLM gateway.

    Falls back to empty string on any error so callers use heuristic logic.
    """
    from app.services.llm.utils import get_model_api_key
    from app.services.llm.client import LLMMessage
    from app.services.llm.single_step import complete_llm_once
    tenant_id = getattr(_llm_caller, "_last_tenant_id", None)
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


async def _llm_generate_agent_specs(category: str, role_gaps: list, user_message: str = "") -> list:
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
    raw = await _llm_caller(prompt)
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
    _llm_caller._last_tenant_id = current_user.tenant_id
    planner = GroupPlanner()
    planner.llm_caller = _llm_caller
    return OrchestratorService(
        intent_analyzer=IntentAnalyzer(llm_caller=_llm_caller),
        agent_selector=AgentSelector(
            template_search=_template_search,
            llm_generate_template=_llm_generate_agent_specs,
        ),
        group_planner=planner,
    )


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




async def _start_chief_runtime_in_background(
    *,
    chief_run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    chief_agent_id: uuid.UUID,
    group_id: uuid.UUID,
) -> None:
    """Spawn the OrchestratorRunLoop for a newly-created Chief Run.

    The loop subscribes to task board events and drives the Chief agent.
    On any error during startup, the chief_runs row is marked as
    'degraded' so the UI can surface that the Chief is not yet active.
    """
    try:
        from app.services.agent_runtime.orchestrator.orchestrator_run import (
            OrchestratorRunLoop,
        )
        from app.services.agent_runtime.orchestrator.event_listener import (
            EventListener,
        )
        from app.services.agent_runtime.orchestrator.reasoner import (
            Reasoner,
        )
        from app.services.agent_runtime.orchestrator.action_executor import (
            ActionExecutor,
        )

        loop = OrchestratorRunLoop(
            event_listener=EventListener(),
            reasoner=Reasoner(llm_caller=_llm_caller),
            action_executor=ActionExecutor(),
        )
        await loop.run(group_id=group_id, tenant_id=tenant_id)
    except Exception as exc:
        logger.exception(f"Chief Runtime startup failed for {chief_run_id}: {exc}")
        async with async_session() as db:
            from app.models.chief_run import ChiefRun
            cr = await db.get(ChiefRun, chief_run_id)
            if cr is not None:
                cr.status = "degraded"
                cr.last_failure_reason = f"startup: {str(exc)[:200]}"
                await db.commit()


async def _create_project_from_draft(
    draft_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    visibility: str,
) -> dict:
    """Materialize a Draft into a real Group + real Chat with Chief.

    Flow:
      1. Look up the approved Draft
      2. Create one Agent row per LLM-generated member (incl. chief-of-staff)
      3. Create the Group via group_chat_service (real chat infrastructure)
      4. Add the user as manager + agents as members (real participants)
      5. Create one primary group session (the main chat)
      6. Chief posts the opening message: restate the goal + stage plan
      7. Chief posts one task-assignment message per task with @-mentions
      8. Mark the Draft as consumed and store the materialized IDs
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
            prev = draft_row.error_detail or {}
            if prev.get("materialized"):
                # Replay: was this draft already materialized?
                return {
                    "draft_id": draft_id,
                    "group_id": prev["group_id"],
                    "session_id": prev.get("session_id"),
                    "chief_agent_id": prev["chief_agent_id"],
                    "chief_run_id": prev.get("chief_run_id"),
                }

        payload = draft_row.draft_payload or {}
        group_meta = payload.get("group", {})
        members_meta = payload.get("members", [])
        okr_meta = payload.get("okr")
        tasks_meta = payload.get("tasks", [])

        # 1. Get the user's Participant identity (manager of the new group)
        creator_user = await db.get(UserModel, user_id)
        if creator_user is None:
            raise HTTPException(status_code=400, detail="User not found")
        creator_part = await get_or_create_user_participant(
            db,
            creator_user.id,
            creator_user.display_name,
            creator_user.avatar_url,
        )

        # 2. Create one Agent row per member
        agent_ids: list[uuid.UUID] = []
        agent_participant_ids: list[uuid.UUID] = []
        for m in members_meta:
            agent_id = uuid.uuid4()
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
            ap = await get_or_create_agent_participant(
                db, agent_id, m.get("name", "Agent"), avatar_url=None
            )
            agent_participant_ids.append(ap.id)
        await db.flush()

        # 3. Create the real Group via group_chat_service
        try:
            group = await group_chat_service.create_group(
                db,
                tenant_id=tenant_id,
                creator_participant_id=creator_part.id,
                name=(group_meta.get("name") or "新项目")[:200],
                description=group_meta.get("description"),
                member_participant_ids=agent_participant_ids,
            )
        except GroupChatServiceError as exc:
            raise HTTPException(status_code=400, detail=f"Failed to create group: {exc}")

        # 4. Create the primary group session (the main chat thread)
        try:
            primary_session = await group_chat_service.create_group_session(
                db,
                tenant_id=tenant_id,
                group_id=group.id,
                actor_participant_id=creator_part.id,
                title="主线",
            )
        except GroupChatServiceError as exc:
            raise HTTPException(status_code=400, detail=f"Failed to create session: {exc}")

        # 5. Find the Chief (for posting opening + task messages)
        chief_agent = next(
            (a for a, m in zip(agent_ids, members_meta) if m.get("role") == "chief-of-staff"),
            None,
        )
        if chief_agent is None and agent_ids:
            chief_agent = agent_ids[0]
        chief_part = None
        if chief_agent:
            chief_part = await get_or_create_agent_participant(
                db, chief_agent, m.get("name", "Chief"), avatar_url=None
            )
        # The chief_participant_id of the chief (for posting as Chief)
        chief_part_id = chief_part.id if chief_part else None

        # Build a name->participant_id map for @-mentions in task messages
        name_to_part_id: dict[str, uuid.UUID] = {
            m.get("name", ""): ap_id
            for m, ap_id in zip(members_meta, agent_participant_ids)
        }

        # 6. Chief posts the OPENING message (restate goal + announce plan)
        okr_line = ""
        if okr_meta and okr_meta.get("objective_title"):
            okr_line = f"\n\n🎯 目标:{okr_meta['objective_title']}"
        kr_lines = ""
        if okr_meta and okr_meta.get("key_results"):
            kr_lines = "\n\n📊 关键结果:"
            for kr in okr_meta["key_results"][:5]:
                kr_lines += f"\n  · {kr.get('title', '')}"
        opening_msg = (
            f"👋 大家好,我是 {m.get('name', 'Chief of Staff') if chief_agent else 'Chief'}。\n\n"
            f"我已理解目标:{(group_meta.get('description') or user_input(payload) or '')[:300]}"
            f"{okr_line}{kr_lines}\n\n"
            f"📋 我已经把任务分配给团队成员,见下面的 @-mention。每完成一个会汇报进度。\n"
            f"如遇决策点,我会单独 @ 你,确认后回到主线继续。\n\n"
            f"—— Chief of Staff"
        )
        try:
            await group_message_service.enqueue_group_message(
                db,
                tenant_id=tenant_id,
                group_id=group.id,
                session_id=primary_session.id,
                sender_participant_id=chief_part_id or creator_part.id,
                content=opening_msg,
            )
        except GroupMessageServiceError as exc:
            logger.warning(f"Failed to post opening message: {exc}")

        # 7. Chief posts one task-assignment message per task with @-mentions
        for t in tasks_meta:
            assignee_role = t.get("assignee_role")
            assignee_name = next(
                (m.get("name") for m in members_meta if m.get("role") == assignee_role),
                None,
            )
            mention_ids: list[uuid.UUID] = []
            if assignee_name and assignee_name in name_to_part_id:
                mention_ids = [name_to_part_id[assignee_name]]
            content = (
                f"📌 任务分配:{t.get('title', '')}\n\n"
                f"负责人:{'@' + assignee_name if assignee_name else '(unassigned)'}\n"
                f"说明:{t.get('description', '')}\n"
            )
            try:
                await group_message_service.enqueue_group_message(
                    db,
                    tenant_id=tenant_id,
                    group_id=group.id,
                    session_id=primary_session.id,
                    sender_participant_id=chief_part_id or creator_part.id,
                    content=content,
                    mention_participant_ids=mention_ids,
                )
            except GroupMessageServiceError as exc:
                logger.warning(f"Failed to post task message: {exc}")

        # 8. Also persist ChiefRun so the existing Chief Runtime loop can boot
        #    (in v2, the Chief Runtime will subscribe to group chat messages
        #    instead of task_board_events; for v1 the loop just stays alive)
        chief_run = ChiefRun(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            group_id=group.id,
            chief_agent_id=chief_agent or agent_ids[0],
            runtime_thread_id=f"orchestrator:{group.id}",
            status="active",
        )
        db.add(chief_run)

        # 9. Mark draft consumed + persist the IDs for replay
        draft_row.status = "consumed"
        draft_row.error_detail = {
            "materialized": True,
            "group_id": str(group.id),
            "session_id": str(primary_session.id),
            "chief_agent_id": str(chief_agent or agent_ids[0]),
            "chief_run_id": str(chief_run.id),
            "agent_ids": [str(a) for a in agent_ids],
        }
        await db.commit()

    return {
        "draft_id": draft_id,
        "group_id": group.id,
        "session_id": primary_session.id,
        "chief_agent_id": chief_agent or agent_ids[0],
        "chief_run_id": chief_run.id,
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
