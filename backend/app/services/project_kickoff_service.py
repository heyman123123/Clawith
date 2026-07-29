"""Draft and send project execution-group kickoff messages."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.participant import Participant
from app.models.project import ProjectWorkflow
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_message_service
from app.services.group_message_service import GroupMessageServiceError
from app.services.participant_identity import get_or_create_user_participant
from app.services.project_team_builder import build_team_wakeup_message

_GROUP_SESSION_TYPE = "group"


class ProjectKickoffError(RuntimeError):
    """Raised when kickoff draft/send cannot proceed."""


async def _load_workflow(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ProjectWorkflow:
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
        )
    )
    if workflow is None:
        raise ProjectKickoffError("项目工作流不存在")
    if workflow.status != "active":
        raise ProjectKickoffError("项目尚未就绪，无法启动团队")
    if workflow.group_id is None:
        raise ProjectKickoffError("项目执行群尚未创建")
    return workflow


async def _resolve_execution_context(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
) -> dict:
    if workflow.group_leader_agent_id is None:
        raise ProjectKickoffError("项目缺少群主")

    leader_agent = await db.get(Agent, workflow.group_leader_agent_id)
    if leader_agent is None:
        raise ProjectKickoffError("项目群主不存在")

    leader_participant = await db.scalar(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id == leader_agent.id,
        )
    )
    if leader_participant is None:
        raise ProjectKickoffError("项目群主身份不存在")

    session = await db.scalar(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == workflow.tenant_id,
            ChatSession.session_type == _GROUP_SESSION_TYPE,
            ChatSession.group_id == workflow.group_id,
            ChatSession.deleted_at.is_(None),
        )
        .order_by(ChatSession.created_at, ChatSession.id)
        .limit(1)
    )
    if session is None:
        raise ProjectKickoffError("项目执行会话不存在")

    return {
        "group_id": workflow.group_id,
        "session_id": session.id,
        "leader_participant_id": leader_participant.id,
        "leader_name": leader_participant.display_name or leader_agent.name,
    }


def _kickoff_system_prompt(*, leader_name: str) -> str:
    return (
        f"你帮助用户起草一条发给项目群主「{leader_name}」的启动消息。"
        "用中文，纯文本，不要 Markdown 代码块，不要解释。"
        f"正文必须以 @{leader_name} 开头。"
        "内容需结合项目需求：说明目标、请群主拆分工作并 @ 成员分派、约定验收与汇报方式。"
        "语气专业简洁，像用户本人在群里发的第一条指令。"
    )


async def _llm_draft_kickoff(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    leader_name: str,
    instructions: str | None,
) -> str:
    from app.services.llm.model_resolution import load_active_model
    from app.services.llm.utils import LLMMessage, create_llm_client, get_model_api_key

    tenant = await db.get(Tenant, workflow.tenant_id)
    model = await load_active_model(
        db,
        model_id=tenant.default_model_id if tenant is not None else None,
        tenant_id=workflow.tenant_id,
    )
    if model is None:
        raise ProjectKickoffError("请先在公司设置中配置可用的默认模型")
    api_key = get_model_api_key(model)
    if not api_key:
        raise ProjectKickoffError("默认模型没有 API Key，请在公司设置中补充配置")

    roles = (workflow.team_plan or {}).get("roles") or []
    role_lines = "\n".join(
        f"- {role.get('name')}{'（群主）' if role.get('is_group_leader') else ''}：{role.get('duties') or ''}"
        for role in roles
        if isinstance(role, dict)
    )
    user_prompt = (
        f"项目名称：{workflow.name}\n"
        f"项目需求：\n{workflow.requirements}\n\n"
        f"团队角色：\n{role_lines or '- 未提供'}\n"
    )
    if instructions and instructions.strip():
        user_prompt += f"\n额外要求：\n{instructions.strip()}\n"

    client = create_llm_client(
        provider=model.provider,
        api_key=api_key,
        model=model.model,
        base_url=model.base_url,
        timeout=float(model.request_timeout or 120),
    )
    response = await client.complete(
        messages=[
            LLMMessage(role="system", content=_kickoff_system_prompt(leader_name=leader_name)),
            LLMMessage(role="user", content=user_prompt),
        ],
        temperature=0.4,
        max_tokens=1200,
    )
    content = (response.content or "").strip()
    if not content:
        raise ProjectKickoffError("模型未返回启动文案")
    mention = f"@{leader_name}"
    if mention not in content:
        content = f"{mention}\n\n{content}"
    return content


async def draft_kickoff_message(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    instructions: str | None = None,
) -> dict:
    del user_id  # reserved for future per-user draft preferences
    workflow = await _load_workflow(db, workflow_id=workflow_id, tenant_id=tenant_id)
    context = await _resolve_execution_context(db, workflow=workflow)
    try:
        content = await _llm_draft_kickoff(
            db,
            workflow=workflow,
            leader_name=context["leader_name"],
            instructions=instructions,
        )
    except Exception:
        plan = dict(workflow.team_plan or {})
        plan.setdefault("project_name", workflow.name)
        plan.setdefault("requirements", workflow.requirements)
        content = build_team_wakeup_message(plan)

    return {
        "content": content,
        "leader_participant_id": context["leader_participant_id"],
        "leader_name": context["leader_name"],
        "group_id": context["group_id"],
        "session_id": context["session_id"],
    }


async def send_kickoff_message(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user: User,
    content: str,
) -> dict:
    workflow = await _load_workflow(db, workflow_id=workflow_id, tenant_id=tenant_id)
    context = await _resolve_execution_context(db, workflow=workflow)

    if workflow.kickoff_sent_at is not None:
        return {
            "group_id": context["group_id"],
            "session_id": context["session_id"],
            "message_id": None,
            "already_sent": True,
        }

    text = (content or "").strip()
    if not text:
        raise ProjectKickoffError("启动文案不能为空")
    mention = f"@{context['leader_name']}"
    if mention not in text:
        text = f"{mention}\n\n{text}"

    human_participant = await get_or_create_user_participant(
        db,
        user.id,
        user.display_name,
        user.avatar_url,
    )
    message_id = uuid.uuid4()
    try:
        intake = await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=context["group_id"],
            session_id=context["session_id"],
            sender_participant_id=human_participant.id,
            content=text,
            mention_participant_ids=[context["leader_participant_id"]],
            message_id=message_id,
        )
    except GroupMessageServiceError as exc:
        raise ProjectKickoffError(str(exc)) from exc

    workflow.kickoff_sent_at = datetime.now(UTC)
    workflow.updated_at = datetime.now(UTC)
    await db.flush()

    return {
        "group_id": context["group_id"],
        "session_id": context["session_id"],
        "message_id": intake.message.id,
        "already_sent": False,
    }
