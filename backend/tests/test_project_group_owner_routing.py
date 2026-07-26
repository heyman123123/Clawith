from pathlib import Path


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
    source = Path("app/api/projects.py").read_text()
    assert "AgentAgentRelationship" in source
    assert 'relation="project_teammate"' in source
    assert "build_team_wakeup_message" in source
    assert "group_message_service.enqueue_group_message" in source


def test_project_group_is_exposed_only_after_all_members_are_ready() -> None:
    source = Path("app/api/projects.py").read_text()
    assert "async def _provision_project_agents" in source
    assert "项目团队缺少可用主模型" in source
    assert "agent.status not in {\"running\", \"idle\"}" in source
    create_start = source.index("async def create_project(")
    create_end = source.index('@router.post("/{workflow_id}/decision-group"', create_start)
    create_route = source[create_start:create_end]
    assert create_route.index("await _provision_project_agents(") < create_route.index(
        "await group_chat_service.create_group("
    )
    assert "_background_project_agent_setup" not in source


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
    task_source = Path("app/services/project_task_service.py").read_text()

    assert "decision_group_id" in project_source
    assert "· 决策群" in project_source
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


def test_shareholder_group_auto_includes_active_project_leaders() -> None:
    api_source = Path("app/api/projects.py").read_text()
    create_start = api_source.index("async def create_shareholder_group(")
    create_end = api_source.index("async def get_shareholder_board(", create_start)
    create_route = api_source[create_start:create_end]

    # The shareholder group creator must look up every active project leader
    # and seed the group with them, then promote the first leader to 群主.
    assert "ProjectWorkflow.group_leader_agent_id == Agent.id" in create_route
    assert 'ProjectWorkflow.status == "active"' in create_route
    assert "member_participant_ids=leader_participant_ids" in create_route
    assert "group.owner_agent_id = leader_rows[0].id" in create_route
    assert "自动包含所有项目负责人 Agent" in create_route


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
    api_source = Path("app/api/projects.py").read_text()
    helper_start = api_source.index(
        "async def _sync_shareholder_group_with_project_leader("
    )
    helper_end = api_source.index("async def _ensure_project_decision_group(", helper_start)
    helper_block = api_source[helper_start:helper_end]

    # The helper must guard on ShareholderGroup existence, look up the
    # project leader's participant, backfill owner_agent_id when missing,
    # and add / re-activate membership in a single transaction.
    assert "ShareholderGroup.tenant_id == tenant_id" in helper_block
    assert (
        "shareholder_group_entity.owner_agent_id = leader_agent.id"
        in helper_block
    )
    assert "GroupMember.participant_id == leader_participant.id" in helper_block
    assert "existing_membership.removed_at = None" in helper_block
    assert "role=\"member\"" in helper_block

    # Both the create path and the repair path must invoke the helper so a
    # newly activated project always shows up in the shareholder group.
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
    assert (
        "_sync_shareholder_group_with_project_leader(" in create_route
    )
    assert (
        "_sync_shareholder_group_with_project_leader(" in provision_route
    )
