"""Shared project team provisioning for Projects API and HR select flow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.agent import Agent
from app.models.group import Group, GroupMember
from app.models.org import AgentAgentRelationship
from app.models.participant import Participant
from app.models.project import (
    ProjectWorkflow,
    ProjectWorkflowMember,
    ShareholderGroup,
)
from app.models.tenant import Tenant
from app.services import group_chat_service
from app.services.access_relationships import ensure_access_granted_platform_relationships
from app.services.agent_manager import agent_manager
from app.services.governance_membership import select_decision_group_members
from app.services.group_chat_service import GroupChatServiceError
from app.services.llm.model_resolution import load_active_model
from app.services.participant_identity import get_or_create_user_participant
from app.services.project_team_builder import (
    build_team_wakeup_message,
    materialize_role_agent,
)
from app.services.storage import store_agent_bytes

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ProjectProvisioningError(RuntimeError):
    """A project team was not ready to receive work."""


async def project_default_model_id(
    db: AsyncSession,
    *,
    tenant: Tenant | None,
    tenant_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the tenant default only when it is usable by project Agents."""
    configured_model_id = tenant.default_model_id if tenant is not None else None
    model = await load_active_model(
        db,
        model_id=configured_model_id,
        tenant_id=tenant_id,
    )
    return model.id if model is not None else None


async def provision_project_agents(
    db: AsyncSession,
    *,
    agents: list[tuple[dict, Agent, Participant]],
    creator_id: uuid.UUID,
    tenant_id: uuid.UUID,
    default_model_id: uuid.UUID | None,
) -> None:
    """Make every member executable before exposing the project group."""
    for role, agent, _ in agents:
        active_model = await load_active_model(
            db,
            model_id=agent.primary_model_id,
            tenant_id=tenant_id,
        )
        if active_model is None:
            if default_model_id is None:
                raise ProjectProvisioningError(
                    "项目团队缺少可用主模型。请先在企业模型池启用并设置默认模型，再创建或修复项目。"
                )
            agent.primary_model_id = default_model_id

        await ensure_access_granted_platform_relationships(
            db,
            agent,
            created_by_user_id=creator_id,
        )
        if agent.status not in {"running", "idle"}:
            await agent_manager.initialize_agent_files(
                db,
                agent,
                personality=str(role.get("personality") or ""),
                boundaries=str(role.get("boundaries") or ""),
            )
            soul = str(role.get("soul") or "").strip()
            if soul:
                await store_agent_bytes(agent.id, "soul.md", soul.encode("utf-8"))
            if agent.agent_type == "native":
                agent.status = "idle"
                agent.last_active_at = datetime.now(UTC)
            else:
                await agent_manager.start_container(db, agent)
        if agent.status not in {"running", "idle"}:
            raise ProjectProvisioningError(
                f"成员“{agent.name}”未能完成初始化（状态：{agent.status}）。"
            )
    await db.flush()


async def ensure_team_directory_contacts(
    db: AsyncSession,
    *,
    agents: list[tuple[dict, Agent, Participant]],
    created_by_user_id: uuid.UUID,
) -> None:
    """Make every project teammate a mutual, contactable Directory entry."""
    agent_ids = [agent.id for _, agent, _ in agents]
    existing_result = await db.execute(
        select(AgentAgentRelationship.agent_id, AgentAgentRelationship.target_agent_id).where(
            AgentAgentRelationship.agent_id.in_(agent_ids),
            AgentAgentRelationship.target_agent_id.in_(agent_ids),
        )
    )
    existing = set(existing_result.all())
    for _, source, _ in agents:
        for _, target, _ in agents:
            if source.id == target.id or (source.id, target.id) in existing:
                continue
            db.add(
                AgentAgentRelationship(
                    id=uuid.uuid4(),
                    agent_id=source.id,
                    target_agent_id=target.id,
                    relation="project_teammate",
                    description="Auto-added because both Agents belong to the same project group.",
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )
    await db.flush()


async def sync_shareholder_group_with_project_leader(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    leader_agent: Agent,
) -> None:
    """Add the project leader to the tenant shareholder group when it exists."""
    shareholder_group = await db.scalar(
        select(ShareholderGroup).where(ShareholderGroup.tenant_id == tenant_id)
    )
    if shareholder_group is None:
        return
    shareholder_group_entity = await db.get(Group, shareholder_group.group_id)
    if (
        shareholder_group_entity is None
        or shareholder_group_entity.deleted_at is not None
    ):
        return
    leader_participant = await db.scalar(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id == leader_agent.id,
        )
    )
    if leader_participant is None:
        return
    if shareholder_group_entity.owner_agent_id is None:
        shareholder_group_entity.owner_agent_id = leader_agent.id
    existing_membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == shareholder_group_entity.id,
            GroupMember.participant_id == leader_participant.id,
        )
    )
    if existing_membership is not None:
        if existing_membership.removed_at is not None:
            existing_membership.removed_at = None
            existing_membership.joined_at = datetime.now(UTC)
        return
    db.add(
        GroupMember(
            id=uuid.uuid4(),
            group_id=shareholder_group_entity.id,
            participant_id=leader_participant.id,
            role="member",
            joined_at=datetime.now(UTC),
            removed_at=None,
            session_read_state={},
        )
    )


async def ensure_project_decision_group(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    human_participant: Participant,
    agents: list[tuple[dict, Agent, Participant]],
) -> None:
    """Create the project governance group once, without duplicating members."""
    if workflow.decision_group_id is not None:
        return
    _, leader_agent, leader_participant = next(
        item for item in agents if item[0]["is_group_leader"]
    )
    governance_members = await select_decision_group_members(
        db,
        tenant_id=workflow.tenant_id,
        leader_participant=leader_participant,
    )
    try:
        decision_group = await group_chat_service.create_group(
            db,
            tenant_id=workflow.tenant_id,
            creator_participant_id=human_participant.id,
            name=f"{workflow.name} · 决策群",
            description=(
                "项目治理与方案评审群。项目群在此汇报进展、成效与卡点；"
                "决策群讨论确认后，由项目负责人下发给项目群执行，并向用户汇报。"
            ),
            member_participant_ids=[participant.id for participant in governance_members],
        )
    except GroupChatServiceError as exc:
        raise ProjectProvisioningError(f"决策群创建失败：{exc}") from exc
    decision_group.owner_agent_id = leader_agent.id
    owner_membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == decision_group.id,
            GroupMember.participant_id == leader_participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if owner_membership is None:
        raise ProjectProvisioningError("Decision group leader membership was not created")
    owner_membership.role = "owner"
    review_session = await group_chat_service.create_group_session(
        db,
        tenant_id=workflow.tenant_id,
        group_id=decision_group.id,
        actor_participant_id=human_participant.id,
        title="方案评审",
    )
    workflow.decision_group_id = decision_group.id
    from app.services import group_message_service

    await group_message_service.enqueue_group_message(
        db,
        tenant_id=workflow.tenant_id,
        group_id=decision_group.id,
        session_id=review_session.id,
        sender_participant_id=human_participant.id,
        content=(
            "【决策群已启动】项目群的阶段进展、交付结果和卡点会汇报到这里。"
            "请先在本群审议方案与风险；需要我确认时，在评审室汇总待决项。"
            "确认后由项目总负责人向项目群下发执行指令，并在本群回报结果。"
        ),
        mention_participant_ids=[leader_participant.id],
        message_id=uuid.uuid4(),
        project_task_dispatch=False,
    )
    await db.flush()


async def provision_team_from_plan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    creator_display_name: str,
    creator_avatar_url: str | None,
    project_name: str,
    requirements: str,
    roles: list[dict],
    template_key: str = "hr_generated",
) -> dict:
    """Create workflow, agents, execution group, decision group, and kickoff message."""
    tenant = await db.get(Tenant, tenant_id)
    default_model_id = await project_default_model_id(
        db,
        tenant=tenant,
        tenant_id=tenant_id,
    )
    if default_model_id is None:
        raise ProjectProvisioningError(
            "项目团队无法创建：请先在企业模型池启用并设置一个默认模型。"
        )

    human_participant = await get_or_create_user_participant(
        db,
        creator_id,
        creator_display_name,
        creator_avatar_url,
    )
    now = datetime.now(UTC)
    workflow = ProjectWorkflow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=creator_id,
        name=project_name.strip(),
        template_key=template_key,
        requirements=requirements.strip(),
        status="provisioning",
        team_plan={"roles": roles, "project_name": project_name.strip(), "requirements": requirements.strip()},
        created_at=now,
        updated_at=now,
    )
    db.add(workflow)

    agents: list[tuple[dict, Agent, Participant]] = []
    for role in roles:
        role_row, agent, participant = await materialize_role_agent(
            db,
            tenant_id=tenant_id,
            creator_id=creator_id,
            project_name=project_name,
            role=role,
            default_model_id=default_model_id,
            tenant=tenant,
        )
        db.add(
            ProjectWorkflowMember(
                id=uuid.uuid4(),
                workflow_id=workflow.id,
                agent_id=agent.id,
                role_key=role_row["key"],
                role_title=role_row["name"],
                is_group_leader=role_row["is_group_leader"],
            )
        )
        agents.append((role_row, agent, participant))
    await db.flush()

    await provision_project_agents(
        db,
        agents=agents,
        creator_id=creator_id,
        tenant_id=tenant_id,
        default_model_id=default_model_id,
    )
    await ensure_team_directory_contacts(
        db,
        agents=agents,
        created_by_user_id=creator_id,
    )

    _, leader_agent, leader_participant = next(
        item for item in agents if item[0]["is_group_leader"]
    )
    try:
        group = await group_chat_service.create_group(
            db,
            tenant_id=tenant_id,
            creator_participant_id=human_participant.id,
            name=f"{workflow.name} · 项目群",
            description=f"由 {leader_agent.name} 负责的项目群。向群主说明需求，群主负责分派并汇报。",
            member_participant_ids=[participant.id for _, _, participant in agents],
        )
    except GroupChatServiceError as exc:
        raise ProjectProvisioningError(str(exc)) from exc

    group.owner_agent_id = leader_agent.id
    owner_membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.participant_id == leader_participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if owner_membership is None:
        raise ProjectProvisioningError("Group leader membership was not created")
    owner_membership.role = "owner"
    session = await group_chat_service.create_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=human_participant.id,
        title="项目协作",
    )
    workflow.group_id = group.id
    workflow.group_leader_agent_id = leader_agent.id
    await ensure_project_decision_group(
        db,
        workflow=workflow,
        human_participant=human_participant,
        agents=agents,
    )

    wake_up_message = build_team_wakeup_message({
        "project_name": workflow.name,
        "requirements": workflow.requirements,
        "roles": roles,
    })
    from app.services import group_message_service
    from app.services.group_message_service import GroupMessageServiceError

    try:
        await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=group.id,
            session_id=session.id,
            sender_participant_id=human_participant.id,
            content=wake_up_message,
            mention_participant_ids=[leader_participant.id],
            message_id=uuid.uuid4(),
        )
    except GroupMessageServiceError as exc:
        raise ProjectProvisioningError(f"Project kickoff could not be created: {exc}") from exc

    workflow.status = "active"
    workflow.updated_at = datetime.now(UTC)
    await db.flush()
    await sync_shareholder_group_with_project_leader(
        db,
        tenant_id=tenant_id,
        leader_agent=leader_agent,
    )

    return {
        "workflow_id": str(workflow.id),
        "group_id": str(group.id),
        "session_id": str(session.id),
        "wake_up_message": wake_up_message,
        "roles": roles,
        "project_name": workflow.name,
        "requirements": workflow.requirements,
        "workflow": workflow,
        "group": group,
        "session": session,
    }
