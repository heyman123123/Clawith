from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.project import ShareholderGroup


def _load_sync_shareholder_group_with_project_leader():
    source = Path("app/services/project_provisioning.py").read_text()
    start = source.index("async def sync_shareholder_group_with_project_leader(")
    end = source.index("async def ensure_project_decision_group(", start)
    namespace = {
        "uuid": uuid,
        "datetime": datetime,
        "UTC": UTC,
        "select": select,
        "AsyncSession": AsyncSession,
        "Agent": Agent,
        "ShareholderGroup": ShareholderGroup,
        "Group": Group,
        "GroupMember": GroupMember,
        "Participant": Participant,
    }
    exec(source[start:end], namespace)
    return namespace["sync_shareholder_group_with_project_leader"]


def test_project_group_messages_force_route_to_group_leader() -> None:
    source = Path("app/services/group_message_service.py").read_text()
    assert "scope.group.owner_agent_id is not None" in source
    assert 'Participant.type == "agent"' in source
    assert "mention_ids = (owner_participant_id,)" in source


def test_owner_role_is_distinct_from_human_manager() -> None:
    source = Path("app/models/group.py").read_text()
    assert "'manager', 'owner', 'member'" in source
    assert "owner_agent_id" in source


def test_project_group_leader_cannot_be_removed_by_a_group_manager() -> None:
    source = Path("app/services/group_chat_service.py").read_text()
    assert "group_owner_required" in source


def test_standard_agent_initialization_materializes_workspace_roots() -> None:
    source = Path("app/services/agent_manager.py").read_text()
    assert '"workspace/.gitkeep"' in source
    assert '"daily_reports/.gitkeep"' in source


def test_project_creation_makes_teammates_mutual_contacts_and_kicks_off_leader() -> None:
    source = Path("app/services/project_provisioning.py").read_text()
    assert "AgentAgentRelationship" in source
    assert 'relation="project_teammate"' in source
    projects_source = Path("app/api/projects.py").read_text()
    assert "provision_team_from_plan" in projects_source
    assert "group_message_service.enqueue_group_message" in source


def test_project_group_is_exposed_only_after_all_members_are_ready() -> None:
    source = Path("app/services/project_provisioning.py").read_text()
    assert "async def provision_project_agents" in source
    assert "项目团队缺少可用主模型" in source
    assert "agent.status not in {\"running\", \"idle\"}" in source
    create_start = Path("app/api/projects.py").read_text().index("async def create_project(")
    create_end = Path("app/api/projects.py").read_text().index('@router.post("/{workflow_id}/decision-group"', create_start)
    create_route = Path("app/api/projects.py").read_text()[create_start:create_end]
    assert "provision_team_from_plan" in create_route
    assert create_route.index("provision_team_from_plan(") < create_route.index("return result")
    assert "_background_project_agent_setup" not in Path("app/api/projects.py").read_text()


def test_project_owner_can_repair_a_previously_creating_team() -> None:
    source = Path("app/api/projects.py").read_text()
    assert '@router.post("/{workflow_id}/provision", response_model=ProjectOut)' in source
    assert "Project workflow not found" in source
    assert "without requiring administrator access" in source
    assert "ProjectProvisioningError" in source
    assert 'workflow.status = "active"' in source


def test_decision_reply_can_be_an_ai_modification_instruction() -> None:
    source = Path("app/api/projects.py").read_text()
    assert 'intent: Literal["decision", "modification"]' in source
    assert "【用户修改指令】" in source
    assert "更新相关任务、依赖、负责人或验收标准" in source


def test_decision_ai_draft_is_generated_without_answering_or_notifying_group() -> None:
    source = Path("app/api/projects.py").read_text()
    start = source.index("async def generate_project_decision_draft(")
    end = source.index('@router.post("/groups/{group_id}/decisions/{decision_id}/reply"', start)
    draft_route = source[start:end]

    assert 'ProjectDecision.status == "pending"' in draft_route
    assert "decision.status =" not in draft_route
    assert "decision.response =" not in draft_route
    assert "enqueue_group_message" not in draft_route
    assert "<think>/<thinking>" in draft_route


def test_project_governance_uses_a_separate_decision_group_for_review() -> None:
    project_source = Path("app/api/projects.py").read_text()
    provisioning_source = Path("app/services/project_provisioning.py").read_text()
    task_source = Path("app/services/project_task_service.py").read_text()

    assert "decision_group_id" in project_source
    assert "· 决策群" in provisioning_source
    assert "_decision_review_group_filter" in project_source
    assert "group_id=decision.group_id" in project_source
    assert "【项目群汇报 → 决策群】" in task_source
    assert "review_group_id=workflow.decision_group_id" in task_source


def test_project_overview_exposes_progress_responsibility_and_blockers() -> None:
    source = Path("app/api/projects.py").read_text()
    assert '@router.get("/groups/{group_id}/overview"' in source
    assert "latest_outcome" in source
    assert "progress_percent" in source
    assert "ProjectBlockerOut" in source


def test_ai_operations_provides_tenant_scoped_failure_diagnostics() -> None:
    source = Path("app/api/enterprise.py").read_text()
    assert '@router.get("/ai-operations/runs/{run_id}")' in source
    assert "input_context" in source
    assert "return_content" in source
    assert "AgentRunEvent.run_id == run_id" in source


def test_shareholder_decisions_route_to_project_governance_before_execution() -> None:
    model_source = Path("app/models/project.py").read_text()
    api_source = Path("app/api/projects.py").read_text()

    assert "class ShareholderGroup" in model_source
    assert "class ShareholderDispatch" in model_source
    assert '@router.post("/groups/{group_id}/shareholder-dispatch")' in api_source
    assert "【股东群确认决策】" in api_source
    assert "group_id=workflow.decision_group_id" in api_source
    assert "project_task_dispatch=False" in api_source


def test_shareholder_group_uses_governance_seeder_on_create() -> None:
    api_source = Path("app/api/projects.py").read_text()
    create_start = api_source.index("async def create_shareholder_group(")
    create_end = api_source.index("async def get_shareholder_board(", create_start)
    create_route = api_source[create_start:create_end]

    assert "ensure_shareholder_group" in create_route


def test_agent_completion_auto_routes_to_group_owner_in_leader_led_project_groups() -> None:
    source = Path("app/services/group_message_service.py").read_text()

    # The existing user→group owner collapse must remain in place.
    assert 'scope.role == "user" and scope.group.owner_agent_id is not None' in source
    assert "mention_ids = (owner_participant_id,)" in source

    # The new branch covers every Agent completion in a leader-led group.
    assert 'scope.role == "assistant" and scope.group.owner_agent_id is not None' in source
    assert (
        "owner_participant_id != scope.participant.id" in source
    )
    assert (
        "mention_ids = mention_ids + (owner_participant_id,)" in source
    )


def test_group_runtime_instruction_requires_leader_mention_on_completion() -> None:
    source = Path("app/services/agent_runtime/model_step_service.py").read_text()
    group_context_source = Path(
        "app/services/agent_runtime/group_context_builder.py"
    ).read_text()

    assert "Project / shareholder groups are leader-led" in source
    assert "group_context.group.owner_agent_id" in source
    assert "write the literal `@<群主 display name>`" in source
    assert "Runtime also auto-routes the message to the 群主" in source

    # The owner_agent_id is exposed to the model so the instruction is
    # actionable instead of a vague reminder.
    assert '"owner_agent_id":' in group_context_source


def test_project_activation_syncs_shareholder_group_with_leader() -> None:
    provisioning_source = Path("app/services/project_provisioning.py").read_text()
    helper_start = provisioning_source.index(
        "async def sync_shareholder_group_with_project_leader("
    )
    helper_end = provisioning_source.index("async def ensure_project_decision_group(", helper_start)
    helper_block = provisioning_source[helper_start:helper_end]

    assert "ShareholderGroup.tenant_id == tenant_id" in helper_block
    assert "shareholder_group_entity.owner_agent_id is None" in helper_block
    assert (
        "shareholder_group_entity.owner_agent_id = leader_agent.id"
        in helper_block
    )
    assert "GroupMember.participant_id == leader_participant.id" in helper_block
    assert "existing_membership.removed_at = None" in helper_block
    assert "role=\"member\"" in helper_block

    api_source = Path("app/api/projects.py").read_text()
    create_start = api_source.index("async def create_project(")
    create_end = api_source.index(
        "@router.post(\"/{workflow_id}/decision-group\"", create_start
    )
    create_route = api_source[create_start:create_end]
    provision_start = api_source.index("async def provision_project_team(")
    provision_end = api_source.index(
        "@router.get(\"/groups/{group_id}/tasks\"", provision_start
    )
    provision_route = api_source[provision_start:provision_end]
    assert "provision_team_from_plan(" in create_route
    assert "sync_shareholder_group_with_project_leader(" in provision_route


@pytest.mark.asyncio
async def test_sync_shareholder_group_preserves_board_secretary_owner() -> None:
    """Active project sync must not replace an existing Board Secretary 群主."""
    sync = _load_sync_shareholder_group_with_project_leader()

    tenant_id = uuid.uuid4()
    board_secretary_id = uuid.uuid4()
    leader_agent_id = uuid.uuid4()
    group_id = uuid.uuid4()
    leader_participant_id = uuid.uuid4()

    leader_agent = Agent(
        id=leader_agent_id,
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Project Leader",
        status="idle",
        is_expired=False,
    )
    group = SimpleNamespace(
        id=group_id,
        owner_agent_id=board_secretary_id,
        deleted_at=None,
    )
    shareholder_row = SimpleNamespace(group_id=group_id)
    leader_participant = SimpleNamespace(id=leader_participant_id)

    class _SyncDB:
        def __init__(self) -> None:
            self.added = []

        async def scalar(self, _statement):
            sql = str(_statement).lower()
            if "shareholder_groups" in sql:
                return shareholder_row
            if "group_members" in sql:
                return None
            if "participants" in sql:
                return leader_participant
            return None

        async def get(self, model, obj_id):
            if model is Group and obj_id == group_id:
                return group
            return None

        def add(self, value) -> None:
            self.added.append(value)

    db = _SyncDB()

    await sync(
        db,
        tenant_id=tenant_id,
        leader_agent=leader_agent,
    )

    assert group.owner_agent_id == board_secretary_id
    memberships = [value for value in db.added if isinstance(value, GroupMember)]
    assert len(memberships) == 1
    assert memberships[0].participant_id == leader_participant_id
    assert memberships[0].role == "member"
