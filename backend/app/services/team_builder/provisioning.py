"""Replay-safe materialization of a confirmed intelligent-team draft."""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import can_use_agent
from app.models.agent import Agent, AgentPermission
from app.models.team_builder import TeamBuildDraft, TeamProvisionJob, TeamProvisionMember
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_chat_service, group_file_service, group_message_service
from app.services.agent_manager import agent_manager
from app.services.participant_identity import (
    get_or_create_agent_participant,
    get_or_create_user_participant,
)
from app.services.team_builder.planning import TeamPlanMember, validate_team_plan

logger = logging.getLogger(__name__)
_READY_AGENT_STATUSES = frozenset({"running", "idle"})


class TeamProvisioningError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _leader_instructions(role: TeamPlanMember, goal: str) -> tuple[str, str]:
    if not role.is_leader:
        return "", ""
    personality = (
        "你是这个群的群主和团队编排者。用户只需要与你沟通。"
        "收到目标后，先在群内公开澄清目标和计划，再 @ 合适成员分发任务；"
        "跟踪依赖和阻塞，并在成员完成后向用户给出简洁、可验证的汇总。"
    )
    boundaries = f"团队初始目标：{goal}\n不得把群主职责转交给成员，不得隐藏成员的公开交付。"
    return personality, boundaries


async def _load_scope(
    db: AsyncSession, job: TeamProvisionJob
) -> tuple[TeamBuildDraft, User, list[TeamProvisionMember]]:
    draft_result = await db.execute(select(TeamBuildDraft).where(TeamBuildDraft.id == job.draft_id))
    draft = draft_result.scalar_one_or_none()
    user_result = await db.execute(
        select(User).where(User.id == job.requesting_user_id, User.tenant_id == job.tenant_id, User.is_active.is_(True))
    )
    user = user_result.scalar_one_or_none()
    members_result = await db.execute(
        select(TeamProvisionMember).where(TeamProvisionMember.job_id == job.id).order_by(TeamProvisionMember.created_at)
    )
    members = list(members_result.scalars().all())
    if draft is None or user is None or not isinstance(draft.reviewed_plan, dict):
        raise TeamProvisioningError(
            "team_provision_scope_invalid", "Confirmed team scope is no longer available", retryable=False
        )
    if not members:
        raise TeamProvisioningError("team_provision_members_missing", "Confirmed team has no members", retryable=False)
    return draft, user, members


async def _new_agent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator: User,
    role: TeamPlanMember,
) -> tuple[Agent, uuid.UUID]:
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant is None:
        raise TeamProvisioningError("team_tenant_not_found", "Team tenant is not available", retryable=False)
    agent = Agent(
        name=role.name,
        role_description=role.role_description,
        bio=role.responsibility,
        creator_id=creator.id,
        tenant_id=tenant_id,
        agent_type="native",
        primary_model_id=tenant.default_model_id,
        template_id=role.template_id,
        status="creating",
        access_mode="company",
        company_access_level="use",
        max_llm_calls_per_day=tenant.default_max_llm_calls_per_day or 1000,
        max_triggers=tenant.default_max_triggers or 20,
        min_poll_interval_min=tenant.min_poll_interval_floor or 5,
        webhook_rate_limit=tenant.max_webhook_rate_ceiling or 5,
        heartbeat_interval_minutes=max(240, tenant.min_heartbeat_interval_minutes or 0),
    )
    db.add(agent)
    await db.flush()
    db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"))
    participant = await get_or_create_agent_participant(db, agent.id, agent.name, agent.avatar_url)
    await db.flush()
    return agent, participant.id


async def _initialize_new_agent(
    db: AsyncSession,
    *,
    agent: Agent,
    role: TeamPlanMember,
    goal: str,
) -> None:
    personality, boundaries = _leader_instructions(role, goal)
    try:
        await agent_manager.initialize_agent_files(db, agent, personality=personality, boundaries=boundaries)
        await agent_manager.start_container(db, agent)
    except Exception as exc:
        agent.status = "error"
        raise TeamProvisioningError("team_agent_initialization_failed", f"Could not initialize {agent.name}") from exc


async def _resolve_members(
    db: AsyncSession,
    *,
    job: TeamProvisionJob,
    draft: TeamBuildDraft,
    user: User,
    members: list[TeamProvisionMember],
) -> tuple[list[uuid.UUID], uuid.UUID]:
    plan = validate_team_plan(draft.reviewed_plan)
    specs = {member.member_key: member for member in plan.members}
    participant_ids: list[uuid.UUID] = []
    leader_participant_id: uuid.UUID | None = None

    for record in members:
        role = specs.get(record.member_key)
        if role is None:
            raise TeamProvisioningError(
                "team_member_plan_mismatch", "Provisioned roster differs from approved plan", retryable=False
            )
        agent: Agent | None = None
        if record.agent_id is not None:
            agent_result = await db.execute(
                select(Agent).where(
                    Agent.id == record.agent_id, Agent.tenant_id == job.tenant_id, Agent.deleted_at.is_(None)
                )
            )
            agent = agent_result.scalar_one_or_none()
        if role.source == "existing":
            if agent is None or not await can_use_agent(db, user, agent):
                raise TeamProvisioningError(
                    "team_existing_agent_unavailable", f"Existing agent for {role.name} is unavailable"
                )
        elif agent is None:
            agent, participant_id = await _new_agent(db, tenant_id=job.tenant_id, creator=user, role=role)
            record.agent_id = agent.id
            record.participant_id = participant_id
            record.status = "waiting"
            await db.flush()
            await _initialize_new_agent(db, agent=agent, role=role, goal=plan.goal)

        if agent is None:
            raise TeamProvisioningError("team_agent_missing", f"Agent for {role.name} is missing")
        if agent.status == "error":
            record.status = "failed"
            record.error_code = "team_agent_initialization_failed"
            record.error_message = f"{agent.name} failed to start"
            raise TeamProvisioningError(record.error_code, record.error_message)
        if agent.status not in _READY_AGENT_STATUSES:
            record.status = "waiting"
            raise TeamProvisioningError("team_agent_waiting", f"{agent.name} is still starting")
        if record.participant_id is None:
            participant = await get_or_create_agent_participant(db, agent.id, agent.name, agent.avatar_url)
            record.participant_id = participant.id
        record.status = "ready"
        record.error_code = None
        record.error_message = None
        participant_ids.append(record.participant_id)
        if role.is_leader:
            leader_participant_id = record.participant_id

    if leader_participant_id is None:
        raise TeamProvisioningError("team_leader_missing", "Approved team has no provisioned leader", retryable=False)
    return participant_ids, leader_participant_id


def _team_documents(plan: dict) -> tuple[str, str]:
    members = plan.get("members", [])
    roster = "\n".join(
        f"- {member.get('name', '成员')}：{member.get('role_description', '')}"
        for member in members
        if isinstance(member, dict)
    )
    brief = "# Team Brief\n\n" + json.dumps(plan, ensure_ascii=False, indent=2)
    return brief, "# Team Roster\n\n" + roster


async def provision_job(db: AsyncSession, *, job_id: uuid.UUID) -> TeamProvisionJob:
    """Advance one job. Each durable output is recorded before a retry can occur."""
    result = await db.execute(select(TeamProvisionJob).where(TeamProvisionJob.id == job_id).with_for_update())
    job = result.scalar_one_or_none()
    if job is None:
        raise TeamProvisioningError("team_job_not_found", "Team provisioning job was not found", retryable=False)
    if job.status == "completed":
        return job
    job.status = "validating"
    draft, user, members = await _load_scope(db, job)
    try:
        participant_ids, leader_participant_id = await _resolve_members(
            db, job=job, draft=draft, user=user, members=members
        )
    except TeamProvisioningError as exc:
        job.status = "retryable_failed" if exc.retryable else "failed"
        job.error_code = exc.code
        job.error_message = str(exc)
        await db.flush()
        return job

    if job.group_id is None:
        job.status = "creating_group"
        creator = await get_or_create_user_participant(db, user.id, user.display_name, user.avatar_url)
        group = await group_chat_service.create_group(
            db,
            tenant_id=job.tenant_id,
            creator_participant_id=creator.id,
            name=validate_team_plan(draft.reviewed_plan).group_name,
            description=validate_team_plan(draft.reviewed_plan).goal,
            member_participant_ids=participant_ids,
            leader_participant_id=leader_participant_id,
        )
        session = await group_chat_service.create_group_session(
            db,
            tenant_id=job.tenant_id,
            group_id=group.id,
            actor_participant_id=creator.id,
            title="团队启动",
        )
        job.group_id = group.id
        job.leader_participant_id = leader_participant_id
        job.session_id = session.id
        brief, roster = _team_documents(draft.reviewed_plan)
        await group_file_service.write_workspace_file(
            db,
            tenant_id=job.tenant_id,
            group_id=group.id,
            actor_participant_id=creator.id,
            path="TEAM_BRIEF.md",
            content=brief,
            require_absent=True,
        )
        await group_file_service.write_workspace_file(
            db,
            tenant_id=job.tenant_id,
            group_id=group.id,
            actor_participant_id=creator.id,
            path="TEAM_ROSTER.md",
            content=roster,
            require_absent=True,
        )

    if job.activation_message_id is None:
        if job.group_id is None or job.session_id is None or job.leader_participant_id is None:
            raise TeamProvisioningError("team_activation_scope_invalid", "Created group is incomplete", retryable=False)
        creator = await get_or_create_user_participant(db, user.id, user.display_name, user.avatar_url)
        job.status = "activating"
        activation_message_id = uuid.uuid4()
        intake = await group_message_service.enqueue_group_message(
            db,
            tenant_id=job.tenant_id,
            group_id=job.group_id,
            session_id=job.session_id,
            sender_participant_id=creator.id,
            content="请基于已确认的 TEAM_BRIEF.md 组建工作节奏、公开拆解首批任务，并向我汇报计划。",
            mention_participant_ids=[job.leader_participant_id],
            message_id=activation_message_id,
        )
        job.activation_message_id = intake.message.id
        if not intake.run_handles:
            raise TeamProvisioningError("team_activation_not_accepted", "Group leader activation was not accepted")
    job.status = "completed"
    job.error_code = None
    job.error_message = None
    await db.flush()
    return job
