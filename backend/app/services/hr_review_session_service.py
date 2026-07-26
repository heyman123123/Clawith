"""HR review session orchestration (Session A team building + Session B apply)."""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.chat_session import ChatSession
from app.models.hr_review import HrReviewSession
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_chat_service
from app.services.hr_review_board_seeder import ensure_hr_review_board
from app.services.participant_identity import get_or_create_user_participant
from app.services.project_team_builder import (
    HRPlanningError,
    build_team_wakeup_message,
    validate_team_plan,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class HrReviewError(RuntimeError):
    pass


def _json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise HrReviewError("HR 评审未返回有效 JSON，请重试")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise HrReviewError("HR 评审返回的 JSON 格式无效，请重试") from exc
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
        if not proposal_id or proposal_id in seen_ids:
            raise ValueError("HR proposal id is invalid or duplicated")
        roles = validate_team_plan({"roles": proposal.get("roles")})
        normalized.append({"id": proposal_id, "label": label, "roles": roles})
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
) -> HrReviewSession:
    return await _open_hr_session(
        db,
        tenant_id=tenant_id,
        user=user,
        session_type="team_building",
        title=f"组建团队：{name.strip()[:80]}",
        context_payload={
            "name": name.strip(),
            "requirements": requirements.strip(),
        },
    )


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
    return """你是 HR Recruiter（招聘专员）。在与 HR Org Designer、HR Strategist 讨论后，为用户项目输出至少 3 套不同的团队组建方案。
每套方案必须包含唯一项目群主（is_group_leader=true 且仅一位）。不要套用固定部门模板。
只返回一个 JSON 对象，不要 Markdown，不要解释。格式：
{"proposals":[
  {"id":"proposal_1","label":"方案名称","roles":[
    {"key":"english_snake_case","name":"岗位名称","role_description":"职责与交付物","personality":"工作风格","boundaries":"职责边界","is_group_leader":true}
  ]}
]}"""


async def generate_team_building_proposals(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
) -> HrReviewSession:
    from app.services.llm.model_resolution import load_active_model
    from app.services.llm.utils import LLMMessage, create_llm_client, get_model_api_key

    hr_session = await db.get(HrReviewSession, hr_session_id)
    if hr_session is None or hr_session.session_type != "team_building":
        raise HrReviewError("HR 团队组建 session 不存在")
    if hr_session.status != "open":
        raise HrReviewError("HR 团队组建 session 已关闭")

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
        timeout=float(model.request_timeout or 120),
    )
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
            max_tokens=4000,
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


async def select_proposal(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    proposal_id: str,
    user: User,
) -> dict:
    hr_session = await db.get(HrReviewSession, hr_session_id)
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

    selected = next(
        (item for item in (hr_session.proposals or []) if str(item.get("id")) == proposal_id),
        None,
    )
    if selected is None:
        raise HrReviewError("所选方案不存在")

    context = hr_session.context_payload or {}
    team_plan = {
        "roles": selected["roles"],
        "project_name": str(context.get("name") or ""),
        "requirements": str(context.get("requirements") or ""),
    }
    team_plan["wake_up_message"] = build_team_wakeup_message(team_plan)
    hr_session.selected_proposal_id = proposal_id
    hr_session.status = "user_selected"
    await db.flush()
    return team_plan


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

    hr_session = await db.get(HrReviewSession, hr_session_id)
    if hr_session is None or hr_session.session_type != "governance_topup":
        raise HrReviewError("治理补全 session 不存在")
    if hr_session.status != "open":
        raise HrReviewError("该 HR 评审 session 已选择或完成")
    if user.tenant_id is None:
        raise HrReviewError("用户缺少租户")

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
