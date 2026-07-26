"""Project workflow API: plan a team, then provision a leader-led project group."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent, AgentPermission
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.org import AgentAgentRelationship
from app.models.participant import Participant
from app.models.project import (
    ProjectDecision,
    ProjectWorkflow,
    ProjectWorkflowMember,
    ShareholderDispatch,
    ShareholderGroup,
)
from app.models.task import Task, TaskLog
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_chat_service
from app.services import group_message_service
from app.services.group_chat_service import GroupChatServiceError
from app.services.group_message_service import GroupMessageServiceError
from app.services.participant_identity import get_or_create_user_participant
from app.services.project_team_builder import (
    HRPlanningError,
    build_team_wakeup_message,
    plan_team_with_hr,
    validate_team_plan,
)
from app.services.access_relationships import ensure_access_granted_platform_relationships
from app.services.agent_manager import agent_manager
from app.services.llm.model_resolution import load_active_model
from app.services.llm.utils import LLMMessage, create_llm_client, get_model_api_key


router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectPlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    requirements: str = Field(min_length=1, max_length=20_000)


class TeamRoleIn(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    role_description: str = Field(min_length=1, max_length=500)
    personality: str = Field(default="", max_length=2_000)
    boundaries: str = Field(default="", max_length=2_000)
    is_group_leader: bool = False


class CreateProjectIn(ProjectPlanIn):
    team_plan: dict


class TeamPlanOut(BaseModel):
    planner_name: str
    project_name: str
    requirements: str
    roles: list[TeamRoleIn]
    wake_up_message: str


class ProjectMemberOut(BaseModel):
    agent_id: uuid.UUID
    role_key: str
    role_title: str
    is_group_leader: bool


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    template_key: str
    requirements: str
    status: str
    team_plan: dict
    group_id: uuid.UUID | None
    decision_group_id: uuid.UUID | None
    group_leader_agent_id: uuid.UUID | None
    failure_reason: str | None
    created_at: datetime
    members: list[ProjectMemberOut] = Field(default_factory=list)


class ProjectTaskOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    title: str
    description: str | None
    status: str
    priority: str
    dependency_task_ids: list[str]
    report_to_agent_id: uuid.UUID | None
    is_project_closure: bool
    completed_at: datetime | None
    updated_at: datetime | None


class ProjectBoardTaskOut(ProjectTaskOut):
    latest_outcome: str | None = None


class ProjectBlockerOut(BaseModel):
    task_id: uuid.UUID
    title: str
    agent_name: str
    status: str
    reason: str | None = None


class ProjectGroupOverviewOut(BaseModel):
    project_name: str
    total_tasks: int
    completed_tasks: int
    active_tasks: int
    blocked_tasks: int
    failed_tasks: int
    progress_percent: int
    tasks: list[ProjectBoardTaskOut]
    blockers: list[ProjectBlockerOut]


class ProjectDecisionReplyIn(BaseModel):
    response: str = Field(min_length=1, max_length=12_000)
    intent: Literal["decision", "modification"] = "decision"


class ProjectDecisionDraftIn(BaseModel):
    """Optional user preference to incorporate when drafting a decision reply."""

    instruction: str = Field(default="", max_length=12_000)


class ProjectDecisionDraftOut(BaseModel):
    draft: str


class ProjectDecisionOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    requesting_agent_id: uuid.UUID | None
    requesting_agent_name: str | None
    title: str
    context: str
    status: str
    response: str | None
    created_at: datetime
    responded_at: datetime | None


class ShareholderGroupOut(BaseModel):
    group_id: uuid.UUID
    name: str
    created_at: datetime


class ShareholderProjectOut(BaseModel):
    workflow_id: uuid.UUID
    name: str
    decision_group_id: uuid.UUID
    decision_leader_name: str
    total_tasks: int
    completed_tasks: int
    blocker_count: int


class ShareholderDispatchOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    project_name: str
    content: str
    status: str
    created_at: datetime


class ShareholderBoardOut(BaseModel):
    group_id: uuid.UUID
    projects: list[ShareholderProjectOut]
    dispatches: list[ShareholderDispatchOut]


class ShareholderDispatchIn(BaseModel):
    workflow_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=12_000)


def _tenant_id(user: User) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=403, detail="A tenant is required for project workflows")
    return user.tenant_id


async def _project_out(db: AsyncSession, workflow: ProjectWorkflow) -> ProjectOut:
    result = await db.execute(
        select(ProjectWorkflowMember).where(ProjectWorkflowMember.workflow_id == workflow.id)
    )
    members = [
        ProjectMemberOut(
            agent_id=member.agent_id,
            role_key=member.role_key,
            role_title=member.role_title,
            is_group_leader=member.is_group_leader,
        )
        for member in result.scalars().all()
    ]
    return ProjectOut(
        id=workflow.id,
        name=workflow.name,
        template_key=workflow.template_key,
        requirements=workflow.requirements,
        status=workflow.status,
        team_plan=workflow.team_plan,
        group_id=workflow.group_id,
        decision_group_id=workflow.decision_group_id,
        group_leader_agent_id=workflow.group_leader_agent_id,
        failure_reason=workflow.failure_reason,
        created_at=workflow.created_at,
        members=members,
    )


class ProjectProvisioningError(RuntimeError):
    """A project team was not ready to receive work."""


async def _project_default_model_id(
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


async def _provision_project_agents(
    db: AsyncSession,
    *,
    agents: list[tuple[dict, Agent, Participant]],
    creator_id: uuid.UUID,
    tenant_id: uuid.UUID,
    default_model_id: uuid.UUID | None,
) -> None:
    """Make every member executable before exposing the project group.

    Project groups are an all-or-nothing collaboration surface: publishing a
    group while one member is still ``creating`` makes the roster look valid
    but makes A2A dispatch fail.  This intentionally performs the same file
    and runtime bootstrap as custom Agent creation synchronously.
    """
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
                personality=role["personality"],
                boundaries=role["boundaries"],
            )
            # Native project members execute through the platform's durable
            # Runtime; an optional OpenClaw sidecar is not a readiness
            # prerequisite.  Requiring an image pull here made a transient
            # Docker registry failure leave every team member in ``creating``.
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


async def _ensure_team_directory_contacts(
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


async def _sync_shareholder_group_with_project_leader(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    leader_agent: Agent,
) -> None:
    """Add the project leader to the tenant's shareholder group, backfilling the
    群主 when none has been designated yet.

    The shareholder group is opt-in (it is only created when a human explicitly
    calls POST /projects/shareholder-group), so this helper is a no-op when the
    group does not exist. Active project creation is the authoritative source
    of truth for "who is a project leader"; keeping the membership in sync here
    means human shareholders always see every project leader and the first
    active leader gets promoted to 群主, even when the shareholder group was
    created before any project.
    """
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
    # Backfill the 群主 when none has been designated yet. This covers the
    # case where the shareholder group was created before any active project
    # existed and therefore had no leader to seed.
    if shareholder_group_entity.owner_agent_id is None:
        shareholder_group_entity.owner_agent_id = leader_agent.id
    # Refresh membership. Re-activate a previously removed membership row
    # when the same leader comes back, and create one when the leader has
    # never been a member.
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

async def _ensure_project_decision_group(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    human_participant: Participant,
    agents: list[tuple[dict, Agent, Participant]],
) -> None:
    """Create the project governance group once, without duplicating members.

    The execution group remains where Agents do the work.  The decision group
    is the single user-facing review surface: all project members can provide
    evidence there, while the project leader owns confirmed handoffs back to
    the execution group.
    """
    if workflow.decision_group_id is not None:
        return
    _, leader_agent, leader_participant = next(
        item for item in agents if item[0]["is_group_leader"]
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
            member_participant_ids=[participant.id for _, _, participant in agents],
        )
    except GroupChatServiceError as exc:
        raise HTTPException(status_code=422, detail=f"决策群创建失败：{exc}") from exc
    decision_group.owner_agent_id = leader_agent.id
    owner_membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == decision_group.id,
            GroupMember.participant_id == leader_participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if owner_membership is None:
        raise HTTPException(status_code=500, detail="Decision group leader membership was not created")
    owner_membership.role = "owner"
    review_session = await group_chat_service.create_group_session(
        db,
        tenant_id=workflow.tenant_id,
        group_id=decision_group.id,
        actor_participant_id=human_participant.id,
        title="方案评审",
    )
    workflow.decision_group_id = decision_group.id
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


@router.post("/team-plans", response_model=TeamPlanOut)
async def create_team_plan(
    body: ProjectPlanIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await plan_team_with_hr(
            db,
            tenant_id=_tenant_id(current_user),
            creator_id=current_user.id,
            name=body.name,
            requirements=body.requirements,
        )
    except (ValueError, HRPlanningError) as exc:
        # Keep failed HR attempts in the immutable operations ledger even
        # though this route returns a 422 and the normal request transaction
        # would otherwise roll back.
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create all agents first; only then create their leader-led project group."""
    tenant_id = _tenant_id(current_user)
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Current user is not active")
    try:
        roles = validate_team_plan(body.team_plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tenant = await db.get(Tenant, tenant_id)
    default_model_id = await _project_default_model_id(
        db,
        tenant=tenant,
        tenant_id=tenant_id,
    )
    if default_model_id is None:
        raise HTTPException(
            status_code=422,
            detail="项目团队无法创建：请先在企业模型池启用并设置一个默认模型。",
        )
    human_participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    now = datetime.now(UTC)
    workflow = ProjectWorkflow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=current_user.id,
        name=body.name.strip(),
        template_key="hr_generated",
        requirements=body.requirements.strip(),
        status="provisioning",
        team_plan={**body.team_plan, "roles": roles},
        created_at=now,
        updated_at=now,
    )
    db.add(workflow)
    agents: list[tuple[dict, Agent, Participant]] = []
    for role in roles:
        agent = Agent(
            id=uuid.uuid4(),
            name=role["name"],
            role_description=role["role_description"],
            bio=f"{body.name.strip()} 项目团队成员：{role['role_description']}",
            creator_id=current_user.id,
            tenant_id=tenant_id,
            agent_type="native",
            status="creating",
            primary_model_id=default_model_id,
            access_mode="company",
            company_access_level="use",
            max_llm_calls_per_day=(tenant.default_max_llm_calls_per_day or 1000) if tenant else 1000,
            max_triggers=(tenant.default_max_triggers or 20) if tenant else 20,
            min_poll_interval_min=(tenant.min_poll_interval_floor or 5) if tenant else 5,
            webhook_rate_limit=(tenant.max_webhook_rate_ceiling or 5) if tenant else 5,
            heartbeat_interval_minutes=max(240, tenant.min_heartbeat_interval_minutes or 0) if tenant else 240,
        )
        participant = Participant(
            id=uuid.uuid4(), type="agent", ref_id=agent.id, display_name=agent.name, avatar_url=None
        )
        db.add_all((agent, participant, AgentPermission(agent_id=agent.id, scope_type="company", access_level="use")))
        db.add(
            ProjectWorkflowMember(
                id=uuid.uuid4(), workflow_id=workflow.id, agent_id=agent.id,
                role_key=role["key"], role_title=role["name"], is_group_leader=role["is_group_leader"],
            )
        )
        agents.append((role, agent, participant))
    await db.flush()

    # Do not expose the group or enqueue the wake-up message until every
    # member has a workspace, a usable primary model and a ready runtime.
    try:
        await _provision_project_agents(
            db,
            agents=agents,
            creator_id=current_user.id,
            tenant_id=tenant_id,
            default_model_id=default_model_id,
        )
    except ProjectProvisioningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _ensure_team_directory_contacts(
        db,
        agents=agents,
        created_by_user_id=current_user.id,
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
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    group.owner_agent_id = leader_agent.id
    owner_membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.participant_id == leader_participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if owner_membership is None:
        raise HTTPException(status_code=500, detail="Group leader membership was not created")
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
    await _ensure_project_decision_group(
        db,
        workflow=workflow,
        human_participant=human_participant,
        agents=agents,
    )
    try:
        await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=group.id,
            session_id=session.id,
            sender_participant_id=human_participant.id,
            content=build_team_wakeup_message({
                "project_name": workflow.name,
                "requirements": workflow.requirements,
                "roles": roles,
            }),
            mention_participant_ids=[leader_participant.id],
            message_id=uuid.uuid4(),
        )
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=f"Project kickoff could not be created: {exc}") from exc
    workflow.status = "active"
    workflow.updated_at = datetime.now(UTC)
    await db.flush()
    await _sync_shareholder_group_with_project_leader(
        db,
        tenant_id=tenant_id,
        leader_agent=leader_agent,
    )

    result = await _project_out(db, workflow)
    # Make the session visible to consumers in the returned transaction, while
    # retaining a deliberate local binding as a regression guard for creation order.
    assert session.group_id == group.id
    return result


@router.post("/{workflow_id}/decision-group", response_model=ProjectOut)
async def ensure_project_decision_group(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Upgrade an existing project to the decision-group governance flow."""
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == _tenant_id(current_user),
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    if workflow.group_id is None:
        raise HTTPException(status_code=422, detail="项目群尚未创建，无法建立决策群。")
    human_participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    member_rows = (
        await db.execute(
            select(ProjectWorkflowMember, Agent, Participant)
            .join(Agent, Agent.id == ProjectWorkflowMember.agent_id)
            .join(Participant, (Participant.type == "agent") & (Participant.ref_id == Agent.id))
            .where(ProjectWorkflowMember.workflow_id == workflow.id)
        )
    ).all()
    agents = [
        ({"is_group_leader": member.is_group_leader}, agent, participant)
        for member, agent, participant in member_rows
    ]
    if not agents or not any(role["is_group_leader"] for role, _, _ in agents):
        raise HTTPException(status_code=422, detail="项目团队缺少可用的项目总负责人。")
    await _ensure_project_decision_group(
        db,
        workflow=workflow,
        human_participant=human_participant,
        agents=agents,
    )
    return await _project_out(db, workflow)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    tenant_id = _tenant_id(current_user)
    result = await db.execute(
        select(ProjectWorkflow)
        .where(ProjectWorkflow.tenant_id == tenant_id, ProjectWorkflow.creator_id == current_user.id)
        .order_by(ProjectWorkflow.created_at.desc())
    )
    return [await _project_out(db, workflow) for workflow in result.scalars().all()]


async def _shareholder_group_for_user(
    db: AsyncSession,
    *,
    current_user: User,
) -> tuple[ShareholderGroup, Participant]:
    """Resolve the company governance group only for an active human member."""
    participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    shareholder_group = await db.scalar(
        select(ShareholderGroup)
        .join(GroupMember, GroupMember.group_id == ShareholderGroup.group_id)
        .where(
            ShareholderGroup.tenant_id == _tenant_id(current_user),
            GroupMember.participant_id == participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if shareholder_group is None:
        raise HTTPException(status_code=404, detail="Shareholder group not found or access denied")
    return shareholder_group, participant


@router.get("/shareholder-group", response_model=ShareholderGroupOut | None)
async def get_shareholder_group(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareholderGroupOut | None:
    """Return the tenant's shareholder group when the user is a member."""
    try:
        shareholder_group, _ = await _shareholder_group_for_user(db, current_user=current_user)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    group = await db.get(Group, shareholder_group.group_id)
    if group is None:
        return None
    return ShareholderGroupOut(
        group_id=group.id,
        name=group.name,
        created_at=shareholder_group.created_at,
    )


@router.post("/shareholder-group", response_model=ShareholderGroupOut, status_code=status.HTTP_201_CREATED)
async def create_shareholder_group(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareholderGroupOut:
    """Create one company-level shareholder group for cross-project governance."""
    tenant_id = _tenant_id(current_user)
    existing = await db.scalar(select(ShareholderGroup).where(ShareholderGroup.tenant_id == tenant_id))
    if existing is not None:
        group = await db.get(Group, existing.group_id)
        if group is None:
            raise HTTPException(status_code=409, detail="Shareholder group record is inconsistent")
        return ShareholderGroupOut(group_id=group.id, name=group.name, created_at=existing.created_at)
    participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    leader_rows_result = await db.execute(
        select(Agent)
        .join(ProjectWorkflow, ProjectWorkflow.group_leader_agent_id == Agent.id)
        .where(
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.status == "active",
            ProjectWorkflow.group_leader_agent_id.is_not(None),
            Agent.deleted_at.is_(None),
        )
        .order_by(ProjectWorkflow.created_at.asc())
    )
    leader_rows = list(leader_rows_result.scalars().all())
    seen_participant_ids: set[uuid.UUID] = {participant.id}
    leader_participant_ids: list[uuid.UUID] = []
    for leader_agent in leader_rows:
        leader_participant = await db.scalar(
            select(Participant).where(
                Participant.type == "agent",
                Participant.ref_id == leader_agent.id,
            )
        )
        if leader_participant is None or leader_participant.id in seen_participant_ids:
            continue
        seen_participant_ids.add(leader_participant.id)
        leader_participant_ids.append(leader_participant.id)
    try:
        group = await group_chat_service.create_group(
            db,
            tenant_id=tenant_id,
            creator_participant_id=participant.id,
            name="股东群",
            description=(
                "公司级项目进展、资源管控与跨项目决策群。自动包含所有项目负责人 Agent，"
                "负责人在此接收股东决策并回报进展；确认后按项目下发至对应决策群。"
            ),
            member_participant_ids=leader_participant_ids,
        )
    except GroupChatServiceError as exc:
        raise HTTPException(status_code=422, detail=f"股东群创建失败：{exc}") from exc
    if leader_rows:
        group.owner_agent_id = leader_rows[0].id
    await group_chat_service.create_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=participant.id,
        title="公司治理",
    )
    shareholder_group = ShareholderGroup(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=group.id,
        creator_id=current_user.id,
    )
    db.add(shareholder_group)
    await db.flush()
    return ShareholderGroupOut(
        group_id=group.id,
        name=group.name,
        created_at=shareholder_group.created_at,
    )


@router.get("/groups/{group_id}/shareholder-board", response_model=ShareholderBoardOut)
async def get_shareholder_board(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareholderBoardOut:
    """List routable project decision groups and company decision receipts."""
    shareholder_group, _ = await _shareholder_group_for_user(db, current_user=current_user)
    if shareholder_group.group_id != group_id:
        raise HTTPException(status_code=404, detail="Shareholder board not found for this group")
    workflows = (
        await db.execute(
            select(ProjectWorkflow, Agent.name)
            .outerjoin(Agent, Agent.id == ProjectWorkflow.group_leader_agent_id)
            .where(
                ProjectWorkflow.tenant_id == _tenant_id(current_user),
                ProjectWorkflow.status == "active",
                ProjectWorkflow.decision_group_id.is_not(None),
            )
            .order_by(ProjectWorkflow.created_at.desc())
        )
    ).all()
    dispatch_rows = (
        await db.execute(
            select(ShareholderDispatch, ProjectWorkflow.name)
            .join(ProjectWorkflow, ProjectWorkflow.id == ShareholderDispatch.workflow_id)
            .where(ShareholderDispatch.shareholder_group_id == shareholder_group.id)
            .order_by(ShareholderDispatch.created_at.desc())
            .limit(30)
        )
    ).all()
    workflow_ids = [workflow.id for workflow, _ in workflows]
    task_rows = (
        await db.execute(
            select(Task.project_workflow_id, Task.status).where(Task.project_workflow_id.in_(workflow_ids))
        )
    ).all() if workflow_ids else []
    task_stats: dict[uuid.UUID, dict[str, int]] = {}
    for workflow_id, task_status in task_rows:
        if workflow_id is None:
            continue
        stats = task_stats.setdefault(workflow_id, {"total": 0, "completed": 0, "blockers": 0})
        stats["total"] += 1
        stats["completed"] += int(task_status == "done")
        stats["blockers"] += int(task_status in {"blocked", "failed"})
    return ShareholderBoardOut(
        group_id=group_id,
        projects=[
            ShareholderProjectOut(
                workflow_id=workflow.id,
                name=workflow.name,
                decision_group_id=workflow.decision_group_id,
                decision_leader_name=leader_name or "项目决策群主",
                total_tasks=task_stats.get(workflow.id, {}).get("total", 0),
                completed_tasks=task_stats.get(workflow.id, {}).get("completed", 0),
                blocker_count=task_stats.get(workflow.id, {}).get("blockers", 0),
            )
            for workflow, leader_name in workflows
            if workflow.decision_group_id is not None
        ],
        dispatches=[
            ShareholderDispatchOut(
                id=dispatch.id,
                workflow_id=dispatch.workflow_id,
                project_name=project_name,
                content=dispatch.content,
                status=dispatch.status,
                created_at=dispatch.created_at,
            )
            for dispatch, project_name in dispatch_rows
        ],
    )


@router.post("/groups/{group_id}/shareholder-dispatch")
async def dispatch_shareholder_decision(
    group_id: uuid.UUID,
    body: ShareholderDispatchIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Route one confirmed shareholder decision to selected project decision leaders."""
    shareholder_group, _ = await _shareholder_group_for_user(db, current_user=current_user)
    if shareholder_group.group_id != group_id:
        raise HTTPException(status_code=404, detail="Shareholder board not found for this group")
    workflow_ids = list(dict.fromkeys(body.workflow_ids))
    workflows = (
        await db.execute(
            select(ProjectWorkflow).where(
                ProjectWorkflow.id.in_(workflow_ids),
                ProjectWorkflow.tenant_id == _tenant_id(current_user),
                ProjectWorkflow.status == "active",
                ProjectWorkflow.decision_group_id.is_not(None),
                ProjectWorkflow.group_leader_agent_id.is_not(None),
            )
        )
    ).scalars().all()
    if len(workflows) != len(workflow_ids):
        raise HTTPException(status_code=422, detail="所选项目中存在未就绪的决策群，无法下发。")
    content = body.content.strip()
    dispatch_ids: list[str] = []
    for workflow in workflows:
        assert workflow.decision_group_id is not None
        assert workflow.group_leader_agent_id is not None
        review_session = await db.scalar(
            select(ChatSession).where(
                ChatSession.tenant_id == workflow.tenant_id,
                ChatSession.group_id == workflow.decision_group_id,
                ChatSession.deleted_at.is_(None),
            ).order_by(ChatSession.created_at.asc())
        )
        leader_participant = await db.scalar(
            select(Participant).where(
                Participant.type == "agent",
                Participant.ref_id == workflow.group_leader_agent_id,
            )
        )
        if review_session is None or leader_participant is None:
            raise HTTPException(status_code=422, detail=f"项目“{workflow.name}”的决策群尚未就绪。")
        dispatch = ShareholderDispatch(
            id=uuid.uuid4(),
            shareholder_group_id=shareholder_group.id,
            workflow_id=workflow.id,
            target_decision_group_id=workflow.decision_group_id,
            content=content,
            status="dispatched",
            created_by_user_id=current_user.id,
        )
        db.add(dispatch)
        await group_message_service.enqueue_group_message(
            db,
            tenant_id=workflow.tenant_id,
            group_id=workflow.decision_group_id,
            session_id=review_session.id,
            # The project leader is a guaranteed member of its decision group.
            # The audit receipt keeps the shareholder who confirmed the decision.
            sender_participant_id=leader_participant.id,
            content=(
                f"【股东群确认决策】项目「{workflow.name}」\n{content}\n\n"
                "请决策群主组织本群确认影响、资源与执行方案；确认后向项目群分发任务，"
                "并将进展与风险回报至决策群。"
            ),
            mention_participant_ids=[leader_participant.id],
            message_id=uuid.uuid5(dispatch.id, "shareholder-decision-dispatch"),
            project_task_dispatch=False,
        )
        dispatch_ids.append(str(dispatch.id))
    await db.flush()
    return {"status": "dispatched", "dispatch_ids": dispatch_ids}


@router.post("/{workflow_id}/provision", response_model=ProjectOut)
async def provision_project_team(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Repair a partially-created team without requiring administrator access.

    Older project groups could be made visible before their Agents left the
    ``creating`` state.  The project owner can safely call this endpoint; it
    never grants a group member provisioning or management permissions.
    """
    tenant_id = _tenant_id(current_user)
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")

    try:
        roles = validate_team_plan(workflow.team_plan)
    except ValueError as exc:
        workflow.status = "failed"
        workflow.failure_reason = f"团队方案无效，无法修复：{exc}"
        workflow.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=422, detail=workflow.failure_reason) from exc
    roles_by_key = {role["key"]: role for role in roles}
    member_rows = (
        await db.execute(
            select(ProjectWorkflowMember, Agent)
            .join(Agent, Agent.id == ProjectWorkflowMember.agent_id)
            .where(ProjectWorkflowMember.workflow_id == workflow.id)
        )
    ).all()
    if len(member_rows) != len(roles_by_key):
        workflow.status = "failed"
        workflow.failure_reason = "项目团队成员记录不完整，无法自动修复。"
        workflow.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=422, detail=workflow.failure_reason)
    participant_rows = await db.execute(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id.in_([agent.id for _, agent in member_rows]),
        )
    )
    participants = {participant.ref_id: participant for participant in participant_rows.scalars().all()}
    agents: list[tuple[dict, Agent, Participant]] = []
    for member, agent in member_rows:
        role = roles_by_key.get(member.role_key)
        participant = participants.get(agent.id)
        if role is None or participant is None:
            workflow.status = "failed"
            workflow.failure_reason = "项目团队成员身份不完整，无法自动修复。"
            workflow.updated_at = datetime.now(UTC)
            await db.commit()
            raise HTTPException(status_code=422, detail=workflow.failure_reason)
        agents.append((role, agent, participant))

    tenant = await db.get(Tenant, tenant_id)
    default_model_id = await _project_default_model_id(
        db,
        tenant=tenant,
        tenant_id=tenant_id,
    )
    try:
        await _provision_project_agents(
            db,
            agents=agents,
            creator_id=current_user.id,
            tenant_id=tenant_id,
            default_model_id=default_model_id,
        )
    except ProjectProvisioningError as exc:
        workflow.status = "failed"
        workflow.failure_reason = str(exc)
        workflow.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=422, detail=workflow.failure_reason) from exc

    await _ensure_team_directory_contacts(
        db,
        agents=agents,
        created_by_user_id=current_user.id,
    )
    if workflow.group_id is not None:
        human_participant = await get_or_create_user_participant(
            db,
            current_user.id,
            current_user.display_name,
            current_user.avatar_url,
        )
        await _ensure_project_decision_group(
            db,
            workflow=workflow,
            human_participant=human_participant,
            agents=agents,
        )
    workflow.status = "active"
    workflow.failure_reason = None
    workflow.updated_at = datetime.now(UTC)
    await db.flush()
    if workflow.group_leader_agent_id is not None:
        leader_agent_for_sync = await db.get(Agent, workflow.group_leader_agent_id)
        if leader_agent_for_sync is not None:
            await _sync_shareholder_group_with_project_leader(
                db,
                tenant_id=tenant_id,
                leader_agent=leader_agent_for_sync,
            )
    return await _project_out(db, workflow)


@router.get("/groups/{group_id}/tasks", response_model=list[ProjectTaskOut])
async def list_project_group_tasks(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectTaskOut]:
    """Expose the durable project execution board directly from a project group."""
    workflow = await _project_group_workflow_for_user(db, group_id=group_id, current_user=current_user)
    rows = await db.execute(
        select(Task, Agent.name)
        .join(Agent, Agent.id == Task.agent_id)
        .where(Task.project_workflow_id == workflow.id)
        .order_by(Task.created_at.asc())
    )
    return [
        ProjectTaskOut(
            id=task.id,
            agent_id=task.agent_id,
            agent_name=agent_name,
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            dependency_task_ids=task.dependency_task_ids or [],
            report_to_agent_id=task.report_to_agent_id,
            is_project_closure=task.is_project_closure,
            completed_at=task.completed_at,
            updated_at=task.updated_at,
        )
        for task, agent_name in rows.all()
    ]


@router.get("/groups/{group_id}/overview", response_model=ProjectGroupOverviewOut)
async def get_project_group_overview(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectGroupOverviewOut:
    """Return the project board, visible outcomes, progress, and active blockers."""
    workflow = await _project_group_workflow_for_user(db, group_id=group_id, current_user=current_user)
    rows = (
        await db.execute(
            select(Task, Agent.name)
            .join(Agent, Agent.id == Task.agent_id)
            .where(Task.project_workflow_id == workflow.id)
            .order_by(Task.created_at.asc())
        )
    ).all()
    board_tasks: list[ProjectBoardTaskOut] = []
    blockers: list[ProjectBlockerOut] = []
    for task, agent_name in rows:
        latest_log = await db.scalar(
            select(TaskLog.content)
            .where(TaskLog.task_id == task.id)
            .order_by(TaskLog.created_at.desc())
            .limit(1)
        )
        outcome = latest_log.strip() if isinstance(latest_log, str) and latest_log.strip() else None
        board_tasks.append(
            ProjectBoardTaskOut(
                id=task.id,
                agent_id=task.agent_id,
                agent_name=agent_name,
                title=task.title,
                description=task.description,
                status=task.status,
                priority=task.priority,
                dependency_task_ids=task.dependency_task_ids or [],
                report_to_agent_id=task.report_to_agent_id,
                is_project_closure=task.is_project_closure,
                completed_at=task.completed_at,
                updated_at=task.updated_at,
                latest_outcome=outcome,
            )
        )
        if task.status in {"blocked", "failed"}:
            blockers.append(
                ProjectBlockerOut(
                    task_id=task.id,
                    title=task.title,
                    agent_name=agent_name,
                    status=task.status,
                    reason=outcome or task.description,
                )
            )
    total_tasks = len(board_tasks)
    completed_tasks = sum(task.status == "done" for task in board_tasks)
    active_tasks = sum(task.status in {"pending", "doing"} for task in board_tasks)
    blocked_tasks = sum(task.status == "blocked" for task in board_tasks)
    failed_tasks = sum(task.status == "failed" for task in board_tasks)
    return ProjectGroupOverviewOut(
        project_name=workflow.name,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        active_tasks=active_tasks,
        blocked_tasks=blocked_tasks,
        failed_tasks=failed_tasks,
        progress_percent=round((completed_tasks / total_tasks) * 100) if total_tasks else 0,
        tasks=board_tasks,
        blockers=blockers,
    )


async def _project_group_workflow_for_user(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    current_user: User,
) -> ProjectWorkflow:
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            or_(ProjectWorkflow.group_id == group_id, ProjectWorkflow.decision_group_id == group_id),
            ProjectWorkflow.tenant_id == _tenant_id(current_user),
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    return workflow


def _decision_review_group_filter(group_id: uuid.UUID):
    """Keep legacy decisions in their project group and new ones in review."""
    return or_(
        ProjectDecision.review_group_id == group_id,
        and_(ProjectDecision.review_group_id.is_(None), ProjectDecision.group_id == group_id),
    )


@router.get("/groups/{group_id}/decisions", response_model=list[ProjectDecisionOut])
async def list_project_group_decisions(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectDecisionOut]:
    """Return the active decisions that need this project's human owner."""
    workflow = await _project_group_workflow_for_user(
        db, group_id=group_id, current_user=current_user
    )
    rows = await db.execute(
        select(ProjectDecision, Agent.name)
        .outerjoin(Agent, Agent.id == ProjectDecision.requesting_agent_id)
        .where(
            ProjectDecision.workflow_id == workflow.id,
            ProjectDecision.status == "pending",
            _decision_review_group_filter(group_id),
        )
        .order_by(ProjectDecision.created_at.asc())
    )
    return [
        ProjectDecisionOut(
            id=decision.id,
            task_id=decision.task_id,
            requesting_agent_id=decision.requesting_agent_id,
            requesting_agent_name=requesting_agent_name,
            title=decision.title,
            context=decision.context,
            status=decision.status,
            response=decision.response,
            created_at=decision.created_at,
            responded_at=decision.responded_at,
        )
        for decision, requesting_agent_name in rows.all()
    ]


@router.post(
    "/groups/{group_id}/decisions/{decision_id}/draft",
    response_model=ProjectDecisionDraftOut,
)
async def generate_project_decision_draft(
    group_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: ProjectDecisionDraftIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectDecisionDraftOut:
    """Generate an editable reply without answering or notifying the project group."""
    workflow = await _project_group_workflow_for_user(
        db, group_id=group_id, current_user=current_user
    )
    decision = await db.scalar(
        select(ProjectDecision).where(
            ProjectDecision.id == decision_id,
            ProjectDecision.workflow_id == workflow.id,
            _decision_review_group_filter(group_id),
            ProjectDecision.status == "pending",
        )
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Pending project decision not found")

    tenant = await db.get(Tenant, workflow.tenant_id)
    model = await load_active_model(
        db,
        model_id=tenant.default_model_id if tenant is not None else None,
        tenant_id=workflow.tenant_id,
    )
    if model is None:
        raise HTTPException(
            status_code=422,
            detail="无法生成建议：请先在企业模型池配置可用的默认模型。",
        )
    api_key = get_model_api_key(model)
    if not api_key:
        raise HTTPException(
            status_code=422,
            detail="无法生成建议：默认模型缺少 API Key，请在企业模型池补充配置。",
        )

    client = create_llm_client(
        provider=model.provider,
        api_key=api_key,
        model=model.model,
        base_url=model.base_url,
        timeout=float(model.request_timeout or 120),
    )
    preference = body.instruction.strip()
    try:
        response = await client.complete(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "你是项目负责人的决策助理。根据项目和待决事项，起草一段可直接发送给"
                        "项目总负责人的中文指令。内容应明确用户的决定、修改要求或需要负责人"
                        "进一步处理的事项，简洁、可执行。只输出草稿正文；不要 Markdown 包装、"
                        "解释、前后缀、JSON 或 <think>/<thinking> 标签。"
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        f"项目名称：{workflow.name}\n"
                        f"项目需求：{workflow.requirements}\n\n"
                        f"待决事项：{decision.title}\n"
                        f"待决上下文：{decision.context}\n\n"
                        f"用户补充偏好：{preference or '无，请基于待决上下文给出合理建议。'}"
                    ),
                ),
            ],
            temperature=0.2,
            max_tokens=800,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"生成建议时调用默认模型失败（{type(exc).__name__}）。"
                "请检查默认模型、API Key 与服务地址。"
            ),
        ) from exc
    finally:
        await client.close()

    # The shared client normally moves these tags into reasoning metadata.  Keep
    # this final guard for provider variants that return raw tag-marked content.
    draft = re.sub(
        r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?\s*>",
        "",
        response.content or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    draft = re.sub(r"<think(?:ing)?\b[^>]*>.*", "", draft, flags=re.IGNORECASE | re.DOTALL)
    draft = re.sub(r"</?think(?:ing)?\b[^>]*>", "", draft, flags=re.IGNORECASE).strip()
    if not draft:
        raise HTTPException(status_code=422, detail="默认模型未返回可用的建议内容，请重试。")
    return ProjectDecisionDraftOut(draft=draft)


@router.post("/groups/{group_id}/decisions/{decision_id}/reply", response_model=ProjectDecisionOut)
async def reply_to_project_group_decision(
    group_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: ProjectDecisionReplyIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectDecisionOut:
    """Record a decision or natural-language modification for the group leader."""
    workflow = await _project_group_workflow_for_user(
        db, group_id=group_id, current_user=current_user
    )
    decision = await db.scalar(
        select(ProjectDecision).where(
            ProjectDecision.id == decision_id,
            ProjectDecision.workflow_id == workflow.id,
            _decision_review_group_filter(group_id),
        ).with_for_update()
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Project decision not found")
    if decision.status != "pending":
        raise HTTPException(status_code=409, detail="Project decision has already been answered")
    response = body.response.strip()
    decision.status = "answered"
    decision.response = response
    decision.responded_at = datetime.now(UTC)
    participant = await get_or_create_user_participant(
        db, current_user.id, current_user.display_name, current_user.avatar_url
    )
    if body.intent == "modification":
        leader_instruction = (
            f"【用户修改指令】待决事项「{decision.title}」\n{response}\n\n"
            "请项目总负责人把这条自然语言指令视为对当前项目计划的直接修改："
            "更新相关任务、依赖、负责人或验收标准，按需重新分派，并在群内回报变更与风险。"
        )
    else:
        leader_instruction = (
            f"针对待决事项「{decision.title}」，我的决定是：\n{response}\n\n"
            "请项目总负责人据此调整任务、分派执行并回报结果。"
        )
    try:
        await group_message_service.enqueue_group_message(
            db,
            tenant_id=workflow.tenant_id,
            group_id=decision.group_id,
            session_id=decision.session_id,
            sender_participant_id=participant.id,
            content=leader_instruction,
            message_id=uuid.uuid4(),
            project_task_dispatch=False,
        )
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    requester_name = await db.scalar(
        select(Agent.name).where(Agent.id == decision.requesting_agent_id)
    ) if decision.requesting_agent_id is not None else None
    return ProjectDecisionOut(
        id=decision.id,
        task_id=decision.task_id,
        requesting_agent_id=decision.requesting_agent_id,
        requesting_agent_name=requester_name,
        title=decision.title,
        context=decision.context,
        status=decision.status,
        response=decision.response,
        created_at=decision.created_at,
        responded_at=decision.responded_at,
    )


@router.post("/groups/{group_id}/task-flows", status_code=status.HTTP_201_CREATED)
async def start_project_group_task_flow(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Start the Task loop for a pre-existing project group exactly once per request.

    This is the safe migration path for project groups created before the
    Task-driven loop existed. New project messages already start it implicitly.
    """
    tenant_id = _tenant_id(current_user)
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.group_id == group_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
            ProjectWorkflow.status == "active",
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.group_id == group_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.deleted_at.is_(None),
        ).order_by(ChatSession.created_at.asc())
    )
    if session is None:
        raise HTTPException(status_code=422, detail="Project group session not found")
    participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    try:
        intake = await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session.id,
            sender_participant_id=participant.id,
            content=(
                "启动项目任务流。请以任务完成、依赖解锁和交付回报推进，"
                f"不要使用固定时间表。\n\n项目目标：{workflow.requirements}"
            ),
            message_id=uuid.uuid4(),
        )
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "message_id": str(intake.message.id),
        "run_ids": [str(handle.run_id) for handle in intake.run_handles],
        "status": "started",
    }


@router.get("/{workflow_id}", response_model=ProjectOut)
async def get_project(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    tenant_id = _tenant_id(current_user)
    result = await db.execute(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    return await _project_out(db, workflow)
