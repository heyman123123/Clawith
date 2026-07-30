"""AI-generated workflow drafts; generation never mutates the active workflow."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group, GroupMember
from app.models.group_workflow import GroupWorkflowDraft
from app.models.participant import Participant
from app.models.user import User
from app.services import group_file_service
from app.services.agent_runtime.model_capabilities import (
    PlatformModelConfigurationError,
    resolve_multi_agent_planning_model,
)
from app.services.ai_monitoring import ai_interaction_scope
from app.services.group_file_service import GroupFileServiceError
from app.services.group_workflow.contracts import (
    GroupWorkflowPlanError,
    WorkflowPlan,
    clean_model_json,
    validate_workflow_plan,
)
from app.services.llm.client import LLMMessage
from app.services.llm.single_step import complete_llm_once


class GroupWorkflowPlanningError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_SYSTEM_PROMPT = """You design an evidence-driven lifecycle for one collaboration group.
Return exactly one JSON object and no Markdown. It must have name, source, stages.
source must be \"ai\". Each stage has key, title, goal, requires_approval,
acceptance_criteria, owner_participant_id and items. Each item has item_key, title,
description and assignee_participant_id. Use only supplied participant UUIDs. Make
stages sequential, make evidence concrete, and only use approval gates for human
decision/release/acceptance moments. The group leader coordinates publicly; do not
invent people or use conversational timestamps as progress."""


async def _scope_snapshot(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> dict:
    group_result = await db.execute(
        select(Group).where(Group.id == group_id, Group.tenant_id == tenant_id, Group.deleted_at.is_(None))
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise GroupWorkflowPlanningError("group_not_found", "Group was not found")
    members_result = await db.execute(
        select(GroupMember, Participant)
        .join(Participant, Participant.id == GroupMember.participant_id)
        .where(GroupMember.group_id == group_id, GroupMember.removed_at.is_(None))
        .order_by(GroupMember.joined_at)
    )
    members = [
        {"participant_id": str(participant.id), "name": participant.display_name, "type": participant.type, "role": membership.role}
        for membership, participant in members_result.all()
    ]
    try:
        announcement = await group_file_service.read_announcement(
            db, tenant_id=tenant_id, group_id=group_id, actor_participant_id=actor_participant_id
        )
        announcement_text = announcement.content[:8_000]
    except GroupFileServiceError:
        announcement_text = ""
    return {
        "group": {"name": group.name, "description": group.description or "", "leader_participant_id": str(group.leader_participant_id) if group.leader_participant_id else None},
        "members": members,
        "announcement": announcement_text,
    }


async def generate_draft(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    creator: User,
    actor_participant_id: uuid.UUID,
    request: str,
) -> GroupWorkflowDraft:
    normalized_request = request.strip()
    if not normalized_request:
        raise GroupWorkflowPlanningError("workflow_draft_request_invalid", "Workflow request must not be empty")
    draft = GroupWorkflowDraft(
        tenant_id=tenant_id,
        group_id=group_id,
        creator_user_id=creator.id,
        request={"prompt": normalized_request},
        status="generating",
    )
    db.add(draft)
    await db.flush()
    try:
        snapshot = await _scope_snapshot(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor_participant_id,
        )
        model = await resolve_multi_agent_planning_model(db, tenant_id=tenant_id)
        with ai_interaction_scope(
            tenant_id=tenant_id,
            session_id=f"group:{group_id}",
            source="group_workflow_planning",
        ):
            completion = await complete_llm_once(
                model,
                [
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=json.dumps({"request": normalized_request, **snapshot}, ensure_ascii=False)),
                ],
                tools=None,
                agent_id=None,
                supports_vision=False,
            )
        if completion.tool_calls or not completion.content:
            raise GroupWorkflowPlanningError("workflow_draft_invalid", "Workflow planning model did not return a JSON plan")
        payload = json.loads(clean_model_json(completion.content))
        plan = validate_workflow_plan(
            payload,
            participant_ids={uuid.UUID(member["participant_id"]) for member in snapshot["members"]},
        )
        if plan.source != "ai":
            plan = plan.model_copy(update={"source": "ai"})
        draft.plan = plan.model_dump(mode="json")
        draft.status = "ready"
    except (PlatformModelConfigurationError, GroupWorkflowPlanError, json.JSONDecodeError, GroupWorkflowPlanningError) as exc:
        draft.status = "failed"
        draft.error_code = getattr(exc, "code", "workflow_draft_invalid")
        draft.error_message = str(exc)
    except Exception:  # noqa: BLE001 - model providers expose heterogeneous errors.
        draft.status = "failed"
        draft.error_code = "workflow_draft_model_failed"
        draft.error_message = "Workflow planning could not be completed"
    await db.flush()
    return draft


def confirmed_plan(draft: GroupWorkflowDraft) -> WorkflowPlan:
    if draft.status != "ready" or not isinstance(draft.plan, dict):
        raise GroupWorkflowPlanningError("workflow_draft_not_ready", "Workflow draft is not ready to confirm")
    return validate_workflow_plan(draft.plan)


__all__ = ["GroupWorkflowPlanningError", "confirmed_plan", "generate_draft"]
