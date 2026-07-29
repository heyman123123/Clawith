"""HR review session orchestration (Session A team building + Session B apply)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.hr_review import HrReviewSession
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_chat_service
from app.services.hr_review_board_seeder import (
    HR_REVIEW_BOARD_GROUP_NAME,
    HR_REVIEW_BOARD_GROUP_TYPE,
    ensure_hr_review_board,
)
from app.services.participant_identity import get_or_create_agent_participant, get_or_create_user_participant
from app.services.project_team_builder import (
    HRPlanningError,
    validate_team_plan,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class HrReviewError(RuntimeError):
    pass


_HR_REVIEW_SESSION_MARKER = re.compile(r"<!--hr_review_session:([0-9a-f-]+)-->", re.IGNORECASE)
_FENCED_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def is_hr_review_board_group(group: Group) -> bool:
    return _is_hr_review_board_group(group)


def extract_hr_proposals_from_text(text: str) -> dict | None:
    """Parse HR Secretary proposal JSON from group message or terminal Run output."""
    if not text or not text.strip():
        return None
    marker_match = _HR_REVIEW_SESSION_MARKER.search(text)
    hr_session_id = marker_match.group(1) if marker_match else None
    json_match = _FENCED_JSON_BLOCK.search(text)
    json_text = json_match.group(1).strip() if json_match else text.strip()
    start, end = json_text.find("{"), json_text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(json_text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("hr_review_session_id"):
        hr_session_id = str(payload["hr_review_session_id"]).strip() or hr_session_id
    proposals = payload.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        return None
    return {
        "hr_session_id": hr_session_id,
        "proposals": proposals,
    }


def _merge_context_payload(existing: dict | None, incoming: dict | None) -> dict:
    merged = dict(existing or {})
    for key in ("name", "requirements"):
        incoming_value = str((incoming or {}).get(key) or "").strip()
        if incoming_value and not str(merged.get(key) or "").strip():
            merged[key] = incoming_value
    return merged


async def get_hr_session_for_tenant(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> HrReviewSession | None:
    return await db.scalar(
        select(HrReviewSession)
        .join(Group, Group.id == HrReviewSession.group_id)
        .where(
            HrReviewSession.id == hr_session_id,
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
        )
    )


async def get_hr_session_by_chat_for_tenant(
    db: AsyncSession,
    *,
    chat_session_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> HrReviewSession | None:
    return await db.scalar(
        select(HrReviewSession)
        .join(Group, Group.id == HrReviewSession.group_id)
        .where(
            HrReviewSession.session_id == chat_session_id,
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
        )
        .order_by(HrReviewSession.created_at.desc())
        .limit(1)
    )


async def sync_hr_context_from_user_message(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    content: str,
) -> None:
    """Fill empty team_building context from the user's first in-session message."""
    hr_session = await get_hr_session_by_chat_for_tenant(
        db,
        chat_session_id=chat_session_id,
        tenant_id=tenant_id,
    )
    if (
        hr_session is None
        or hr_session.session_type != "team_building"
        or hr_session.status != "open"
    ):
        return
    message_text = content.strip()
    if not message_text:
        return
    context = dict(hr_session.context_payload or {})
    if not str(context.get("requirements") or "").strip():
        context["requirements"] = message_text
    if not str(context.get("name") or "").strip():
        chat_session = await db.get(ChatSession, chat_session_id)
        title = (chat_session.title or "").strip() if chat_session is not None else ""
        if title:
            context["name"] = title
    hr_session.context_payload = context
    await db.flush()


async def process_hr_group_agent_output(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    text: str,
) -> HrReviewSession | None:
    """Persist validated proposals when HR agents emit proposal JSON in a group session."""
    parsed = extract_hr_proposals_from_text(text)
    if parsed is None:
        return None

    hr_session: HrReviewSession | None = None
    raw_session_id = parsed.get("hr_session_id")
    if raw_session_id:
        try:
            hr_session = await get_hr_session_for_tenant(
                db,
                hr_session_id=uuid.UUID(str(raw_session_id)),
                tenant_id=tenant_id,
            )
        except ValueError:
            hr_session = None
    if hr_session is None:
        hr_session = await get_hr_session_by_chat_for_tenant(
            db,
            chat_session_id=chat_session_id,
            tenant_id=tenant_id,
        )
    if hr_session is None or hr_session.session_type != "team_building":
        return None
    try:
        return await attach_proposals(
            db,
            hr_session_id=hr_session.id,
            proposals=parsed["proposals"],
        )
    except (HrReviewError, ValueError):
        return None


def _json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    if not cleaned:
        raise HrReviewError("HR 评审未返回内容，请重试")
    # Drop common chain-of-thought wrappers before looking for JSON.
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    elif cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise HrReviewError("HR 评审未返回有效 JSON，请重试（可能被截断，请再试一次）")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise HrReviewError("HR 评审返回的 JSON 格式无效或被截断，请重试") from exc
    if not isinstance(value, dict):
        raise HrReviewError("HR 评审返回的 JSON 格式无效，请重试")
    return value


def validate_team_building_proposals(proposals: list) -> list[dict]:
    """Validate ≥3 team-building proposals, each with roles and one group leader."""
    if not isinstance(proposals, list) or len(proposals) < 3:
        raise ValueError("HR review must include at least 3 proposals")
    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise ValueError("HR proposal is invalid")
        proposal_id = str(proposal.get("id") or f"proposal_{index + 1}").strip()
        label = str(proposal.get("label") or f"方案 {index + 1}").strip()
        card_summary = str(proposal.get("card_summary") or "").strip()
        if not proposal_id or proposal_id in seen_ids:
            raise ValueError("HR proposal id is invalid or duplicated")
        if not card_summary:
            raise ValueError("HR proposal card_summary is required")
        roles = validate_team_plan({"roles": proposal.get("roles")})
        normalized.append({
            "id": proposal_id,
            "label": label,
            "card_summary": card_summary,
            "roles": roles,
        })
        seen_ids.add(proposal_id)
    return normalized


async def _default_primary_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
) -> ChatSession:
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.group_id == group_id,
            ChatSession.session_type == "group",
            ChatSession.is_primary.is_(True),
            ChatSession.deleted_at.is_(None),
        ).limit(1)
    )
    if session is None:
        raise HrReviewError("HR 评审群缺少默认 session，请重试")
    return session


async def _open_hr_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    session_type: str,
    title: str,
    context_payload: dict,
) -> HrReviewSession:
    tenant = await db.get(Tenant, tenant_id)
    model_id = tenant.default_model_id if tenant is not None else None
    group = await ensure_hr_review_board(
        db,
        tenant_id=tenant_id,
        creator_id=user.id,
        model_id=model_id,
    )
    participant = await get_or_create_user_participant(
        db,
        user.id,
        user.display_name,
        user.avatar_url,
    )
    default_session = await _default_primary_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
    )
    chat_session = await group_chat_service.create_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=participant.id,
        title=title,
        parent_session_id=default_session.id,
    )
    hr_session = HrReviewSession(
        id=uuid.uuid4(),
        group_id=group.id,
        session_id=chat_session.id,
        session_type=session_type,
        status="open",
        proposals=[],
        context_payload=context_payload,
    )
    db.add(hr_session)
    await db.flush()
    return hr_session


async def open_team_building_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    name: str,
    requirements: str,
    chat_session_id: uuid.UUID | None = None,
) -> HrReviewSession:
    context_payload = {
        "name": name.strip(),
        "requirements": requirements.strip(),
    }
    if chat_session_id is not None:
        return await attach_team_building_session(
            db,
            tenant_id=tenant_id,
            chat_session_id=chat_session_id,
            context_payload=context_payload,
        )
    return await _open_hr_session(
        db,
        tenant_id=tenant_id,
        user=user,
        session_type="team_building",
        title=f"组建团队：{name.strip()[:80]}",
        context_payload=context_payload,
    )


def _is_hr_review_board_group(group: Group) -> bool:
    return group.group_type == HR_REVIEW_BOARD_GROUP_TYPE or group.name == HR_REVIEW_BOARD_GROUP_NAME


async def attach_team_building_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    context_payload: dict | None = None,
) -> HrReviewSession:
    """Bind an open team_building HR review session to an existing group chat session."""
    existing = await db.scalar(
        select(HrReviewSession)
        .join(Group, Group.id == HrReviewSession.group_id)
        .where(
            HrReviewSession.session_id == chat_session_id,
            Group.tenant_id == tenant_id,
            Group.deleted_at.is_(None),
        )
        .order_by(HrReviewSession.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        if context_payload:
            existing.context_payload = _merge_context_payload(existing.context_payload, context_payload)
            await db.flush()
        return existing

    chat_session = await db.get(ChatSession, chat_session_id)
    if chat_session is None or chat_session.deleted_at is not None:
        raise HrReviewError("聊天 session 不存在")
    if chat_session.tenant_id != tenant_id:
        raise HrReviewError("聊天 session 不属于当前租户")
    if chat_session.group_id is None:
        raise HrReviewError("只能为群聊 session 绑定 HR 评审")

    group = await db.get(Group, chat_session.group_id)
    if group is None or group.deleted_at is not None:
        raise HrReviewError("群聊不存在")
    if not _is_hr_review_board_group(group):
        raise HrReviewError("只能为 HR 评审群 session 绑定团队组建评审")

    merged_context = _merge_context_payload({}, context_payload)
    session_title = (chat_session.title or "").strip()
    if session_title and not str(merged_context.get("name") or "").strip():
        merged_context["name"] = session_title

    hr_session = HrReviewSession(
        id=uuid.uuid4(),
        group_id=group.id,
        session_id=chat_session.id,
        session_type="team_building",
        status="open",
        proposals=[],
        context_payload=merged_context,
    )
    db.add(hr_session)
    await db.flush()
    return hr_session


async def open_governance_topup_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    context_payload: dict | None = None,
) -> HrReviewSession:
    return await _open_hr_session(
        db,
        tenant_id=tenant_id,
        user=user,
        session_type="governance_topup",
        title="治理角色补全评审",
        context_payload=context_payload or {},
    )


async def attach_proposals(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    proposals: list[dict],
) -> HrReviewSession:
    hr_session = await db.get(HrReviewSession, hr_session_id)
    if hr_session is None:
        raise HrReviewError("HR 评审 session 不存在")
    if hr_session.status != "open":
        raise HrReviewError("HR 评审 session 已关闭，无法更新方案")
    if hr_session.session_type == "team_building":
        hr_session.proposals = validate_team_building_proposals(proposals)
    else:
        if not isinstance(proposals, list) or len(proposals) < 3:
            raise ValueError("HR review must include at least 3 proposals")
        hr_session.proposals = proposals
    await db.flush()
    return hr_session


def _team_building_system_prompt() -> str:
    return """你是 HR Recruiter（招聘专员）。为用户项目输出至少 3 套不同的团队组建方案。
每套方案必须包含 card_summary（短摘要）与 roles；每角色必须含 duties、soul、suggested_tools、suggested_permissions。
soul 用简洁完整的 soul.md 正文即可（约 80-200 字），不要长篇大论。
每套方案必须包含唯一项目群主（is_group_leader=true 且仅一位）。不要套用固定部门模板。
只返回一个 JSON 对象，不要 Markdown，不要解释，不要思考过程。格式：
{"proposals":[
  {"id":"proposal_1","label":"方案名称","card_summary":"短摘要","roles":[
    {"key":"english_snake_case","name":"岗位名称","duties":"职责与交付物","soul":"# 岗位\\n简洁 soul.md 正文…","is_group_leader":true,
     "suggested_tools":["group_write_workspace_file"],"suggested_permissions":{"scope_type":"company","access_level":"use"}}
  ]}
]}"""


async def generate_team_building_proposals(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
) -> HrReviewSession:
    hr_session = await get_hr_session_for_tenant(
        db,
        hr_session_id=hr_session_id,
        tenant_id=tenant_id,
    )
    if hr_session is None or hr_session.session_type != "team_building":
        raise HrReviewError("HR 团队组建 session 不存在")
    if hr_session.status != "open":
        raise HrReviewError("HR 团队组建 session 已关闭")

    from app.services.llm.model_resolution import load_active_model
    from app.services.llm.utils import LLMMessage, create_llm_client, get_model_api_key

    context = hr_session.context_payload or {}
    name = str(context.get("name") or "").strip()
    requirements = str(context.get("requirements") or "").strip()
    if not name or not requirements:
        raise HrReviewError("缺少项目名称或需求描述")

    tenant = await db.get(Tenant, tenant_id)
    model = await load_active_model(
        db,
        model_id=tenant.default_model_id if tenant is not None else None,
        tenant_id=tenant_id,
    )
    if model is None:
        raise HRPlanningError("请先在公司设置中配置可用的默认模型")
    api_key = get_model_api_key(model)
    if not api_key:
        raise HRPlanningError("默认模型没有 API Key，请在公司设置中补充配置")

    client = create_llm_client(
        provider=model.provider,
        api_key=api_key,
        model=model.model,
        base_url=model.base_url,
        timeout=float(model.request_timeout or 180),
    )
    max_tokens = int(model.max_output_tokens or 16000)
    if max_tokens < 8000:
        max_tokens = 8000
    try:
        response = await client.complete(
            messages=[
                LLMMessage(role="system", content=_team_building_system_prompt()),
                LLMMessage(
                    role="user",
                    content=f"项目名称：{name}\n项目需求：\n{requirements}",
                ),
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        raise HRPlanningError(
            f"HR 评审调用默认模型失败（{type(exc).__name__}）。请检查公司默认模型、API Key 与服务地址"
        ) from exc
    finally:
        await client.close()

    payload = _json_object(response.content or "")
    raw_proposals = payload.get("proposals")
    if not isinstance(raw_proposals, list):
        raise HrReviewError("HR 评审未返回 proposals 数组")
    return await attach_proposals(db, hr_session_id=hr_session_id, proposals=raw_proposals)


async def _send_hr_selection_receipt(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    hr_session: HrReviewSession,
    project_name: str,
    execution_group_id: uuid.UUID,
    execution_session_id: uuid.UUID,
) -> None:
    """HR Secretary confirmation with execution group redirect hint."""
    from app.services import group_message_service

    secretary = await db.scalar(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.name == "HR Secretary",
            Agent.is_system.is_(True),
            Agent.deleted_at.is_(None),
        ).limit(1)
    )
    if secretary is None:
        return
    secretary_participant = await get_or_create_agent_participant(
        db,
        secretary.id,
        display_name=secretary.name,
        avatar_url=secretary.avatar_url,
    )
    await group_message_service.enqueue_group_message(
        db,
        tenant_id=tenant_id,
        group_id=hr_session.group_id,
        session_id=hr_session.session_id,
        sender_participant_id=secretary_participant.id,
        content=(
            f"【方案已确认】项目「{project_name}」的执行群已创建。"
            f"请进入执行群开始协作（group={execution_group_id}，session={execution_session_id}）。"
        ),
        mention_participant_ids=[],
        message_id=uuid.uuid4(),
        project_task_dispatch=False,
    )


async def select_proposal(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    proposal_id: str,
    user: User,
    fallback_proposals: list | None = None,
    send_kickoff: bool = True,
) -> dict:
    if user.tenant_id is None:
        raise HrReviewError("用户缺少租户")

    hr_session = await get_hr_session_for_tenant(
        db,
        hr_session_id=hr_session_id,
        tenant_id=user.tenant_id,
    )
    if hr_session is None:
        raise HrReviewError("HR 评审 session 不存在")
    if hr_session.status != "open":
        raise HrReviewError("该 HR 评审 session 已选择或完成")

    if hr_session.session_type == "governance_topup":
        return await apply_governance_proposal(
            db,
            hr_session_id=hr_session_id,
            proposal_id=proposal_id,
            user=user,
        )

    if hr_session.session_type != "team_building":
        raise HrReviewError("不支持的 HR session 类型")

    if not hr_session.proposals and fallback_proposals:
        hr_session = await attach_proposals(
            db,
            hr_session_id=hr_session.id,
            proposals=fallback_proposals,
        )

    selected = next(
        (item for item in (hr_session.proposals or []) if str(item.get("id")) == proposal_id),
        None,
    )
    if selected is None:
        raise HrReviewError("所选方案不存在")

    context = hr_session.context_payload or {}
    project_name = str(context.get("name") or "")
    requirements = str(context.get("requirements") or "")

    roles = validate_team_plan({"roles": selected["roles"]})
    from app.services.project_provisioning import ProjectProvisioningError, provision_team_from_plan

    try:
        provisioned = await provision_team_from_plan(
            db,
            tenant_id=user.tenant_id,
            creator_id=user.id,
            creator_display_name=user.display_name,
            creator_avatar_url=user.avatar_url,
            project_name=project_name,
            requirements=requirements,
            roles=roles,
            send_kickoff=send_kickoff,
        )
    except ProjectProvisioningError as exc:
        raise HrReviewError(str(exc)) from exc

    await _send_hr_selection_receipt(
        db,
        tenant_id=user.tenant_id,
        hr_session=hr_session,
        project_name=project_name,
        execution_group_id=uuid.UUID(provisioned["group_id"]),
        execution_session_id=uuid.UUID(provisioned["session_id"]),
    )

    hr_session.selected_proposal_id = proposal_id
    hr_session.status = "completed"
    hr_session.closed_at = datetime.now(UTC)
    await db.flush()

    return {
        "roles": provisioned["roles"],
        "wake_up_message": provisioned["wake_up_message"],
        "project_name": provisioned["project_name"],
        "requirements": provisioned["requirements"],
        "workflow_id": provisioned["workflow_id"],
        "group_id": provisioned["group_id"],
        "session_id": provisioned["session_id"],
        "hr_review_session_id": str(hr_session.id),
    }


async def apply_governance_proposal(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    proposal_id: str,
    user: User,
) -> dict:
    """Apply a governance top-up proposal to governance_role_pools."""
    from app.models.governance import GovernanceRolePool
    from app.models.tenant import Tenant
    from app.services.governance_seeder import _ensure_governance_agent

    if user.tenant_id is None:
        raise HrReviewError("用户缺少租户")
    hr_session = await get_hr_session_for_tenant(
        db,
        hr_session_id=hr_session_id,
        tenant_id=user.tenant_id,
    )
    if hr_session is None or hr_session.session_type != "governance_topup":
        raise HrReviewError("治理补全 session 不存在")
    if hr_session.status != "open":
        raise HrReviewError("该 HR 评审 session 已选择或完成")

    selected = next(
        (item for item in (hr_session.proposals or []) if str(item.get("id")) == proposal_id),
        None,
    )
    if selected is None or not isinstance(selected, dict):
        raise HrReviewError("所选方案不存在")

    tenant = await db.get(Tenant, user.tenant_id)
    model_id = tenant.default_model_id if tenant is not None else None

    enabled: list[str] = []
    disabled: list[str] = []
    created: list[str] = []

    for role_key in selected.get("enable_role_keys") or []:
        key = str(role_key).strip()
        if not key:
            continue
        row = await db.scalar(
            select(GovernanceRolePool).where(
                GovernanceRolePool.tenant_id == user.tenant_id,
                GovernanceRolePool.role_key == key,
            )
        )
        if row is not None:
            row.is_default_enabled = True
            enabled.append(key)

    for role_key in selected.get("disable_role_keys") or []:
        key = str(role_key).strip()
        if not key:
            continue
        row = await db.scalar(
            select(GovernanceRolePool).where(
                GovernanceRolePool.tenant_id == user.tenant_id,
                GovernanceRolePool.role_key == key,
            )
        )
        if row is not None:
            row.is_default_enabled = False
            disabled.append(key)

    for raw in selected.get("create_roles") or []:
        if not isinstance(raw, dict):
            continue
        role_key = str(raw.get("role_key") or "").strip()
        role_type = str(raw.get("role_type") or "review").strip()
        role_title = str(raw.get("role_title") or role_key).strip()
        if not role_key or role_type not in {"decision", "review"}:
            continue
        agent = await _ensure_governance_agent(
            db,
            tenant_id=user.tenant_id,
            creator_id=user.id,
            model_id=model_id,
            role_key=role_key,
            role_type=role_type,
            role_title=role_title or role_key,
        )
        row = await db.scalar(
            select(GovernanceRolePool).where(
                GovernanceRolePool.tenant_id == user.tenant_id,
                GovernanceRolePool.role_key == role_key,
            )
        )
        if row is None:
            db.add(
                GovernanceRolePool(
                    tenant_id=user.tenant_id,
                    agent_id=agent.id,
                    role_type=role_type,
                    role_key=role_key,
                    role_title=role_title or role_key,
                    is_default_enabled=True,
                )
            )
        else:
            row.agent_id = agent.id
            row.role_type = role_type
            row.role_title = role_title or role_key
            row.is_default_enabled = True
        created.append(role_key)

    if not enabled and not disabled and not created:
        # Spec proposals may be prose-only; treat selection as completed audit without pool mutation.
        pass

    hr_session.selected_proposal_id = proposal_id
    hr_session.status = "user_selected"
    await db.flush()
    return {
        "proposal_id": proposal_id,
        "enabled_role_keys": enabled,
        "disabled_role_keys": disabled,
        "created_role_keys": created,
        "label": selected.get("label"),
        "plan": selected.get("plan"),
    }


def hr_session_to_dict(hr_session: HrReviewSession) -> dict:
    return {
        "id": hr_session.id,
        "group_id": hr_session.group_id,
        "session_id": hr_session.session_id,
        "session_type": hr_session.session_type,
        "status": hr_session.status,
        "proposals": hr_session.proposals or [],
        "selected_proposal_id": hr_session.selected_proposal_id,
        "context_payload": hr_session.context_payload or {},
        "created_at": hr_session.created_at,
        "closed_at": hr_session.closed_at,
    }
