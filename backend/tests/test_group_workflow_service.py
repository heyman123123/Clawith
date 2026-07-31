"""Evidence and approval transitions stay deterministic under retries."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.group_workflow import service


@pytest.mark.asyncio
async def test_submit_evidence_completes_item_before_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = uuid.uuid4()
    workflow = SimpleNamespace(id=uuid.uuid4(), leader_participant_id=actor, version=3, group_id=uuid.uuid4())
    stage = SimpleNamespace(id=uuid.uuid4(), title="交付", requires_approval=False)
    item = SimpleNamespace(
        id=uuid.uuid4(),
        title="写报告",
        assignee_participant_id=actor,
        status="in_progress",
        evidence=[],
        blocked_reason=None,
        version=2,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(display_name="Morty")))
    monkeypatch.setattr(service, "_locked_item", AsyncMock(return_value=(workflow, stage, item)))
    recorded = AsyncMock()
    monkeypatch.setattr(service, "_event", recorded)
    transition = SimpleNamespace(workflow=workflow, stage=stage, next_stage=None, leader_action=None)
    reconcile = AsyncMock(return_value=transition)
    monkeypatch.setattr(service, "_reconcile", reconcile)
    monkeypatch.setattr(service, "_refresh_ready_items", AsyncMock(return_value=()))
    leader_action = AsyncMock()
    monkeypatch.setattr(service, "_leader_action", leader_action)

    result = await service.submit_evidence(db, item_id=item.id, actor_participant_id=actor, evidence={"ref": "report.md"})

    assert result.leader_action is None
    assert item.status == "done"
    assert item.evidence == [{"ref": "report.md"}]
    assert item.version == 3
    assert workflow.version == 4
    reconcile.assert_awaited_once()
    leader_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_evidence_skips_member_progress_when_gate_wakes_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = uuid.uuid4()
    workflow = SimpleNamespace(id=uuid.uuid4(), leader_participant_id=actor, version=3, group_id=uuid.uuid4())
    stage = SimpleNamespace(id=uuid.uuid4(), title="验收", requires_approval=True)
    item = SimpleNamespace(
        id=uuid.uuid4(),
        title="合并",
        assignee_participant_id=actor,
        status="in_progress",
        evidence=[],
        blocked_reason=None,
        version=1,
    )
    gate_action = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(service, "_locked_item", AsyncMock(return_value=(workflow, stage, item)))
    monkeypatch.setattr(service, "_event", AsyncMock())
    monkeypatch.setattr(
        service,
        "_reconcile",
        AsyncMock(return_value=SimpleNamespace(workflow=workflow, stage=stage, next_stage=None, leader_action=gate_action)),
    )
    monkeypatch.setattr(service, "_refresh_ready_items", AsyncMock(return_value=()))
    leader_action = AsyncMock()
    monkeypatch.setattr(service, "_leader_action", leader_action)

    result = await service.submit_evidence(
        SimpleNamespace(), item_id=item.id, actor_participant_id=actor, evidence={"ref": "done"}
    )

    assert result.leader_action is gate_action
    leader_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_evidence_does_not_allow_leader_to_complete_member_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader = uuid.uuid4()
    member = uuid.uuid4()
    workflow = SimpleNamespace(id=uuid.uuid4(), leader_participant_id=leader, version=1, group_id=uuid.uuid4())
    stage = SimpleNamespace(id=uuid.uuid4(), title="开发", requires_approval=False)
    item = SimpleNamespace(
        id=uuid.uuid4(), assignee_participant_id=member, status="in_progress",
        evidence=[], blocked_reason=None, version=1, description="成员实现接口",
    )
    monkeypatch.setattr(service, "_locked_item", AsyncMock(return_value=(workflow, stage, item)))

    with pytest.raises(service.GroupWorkflowServiceError) as exc:
        await service.submit_evidence(
            SimpleNamespace(), item_id=item.id, actor_participant_id=leader, evidence={"ref": "leader-note.md"}
        )

    assert exc.value.code == "workflow_item_access_denied"


@pytest.mark.asyncio
async def test_source_code_item_rejects_document_only_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = uuid.uuid4()
    workflow = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(),
        leader_participant_id=uuid.uuid4(), version=1,
    )
    stage = SimpleNamespace(id=uuid.uuid4(), title="开发", requires_approval=False)
    item = SimpleNamespace(
        id=uuid.uuid4(), assignee_participant_id=actor, status="in_progress",
        evidence=[], blocked_reason=None, version=1,
        description="[evidence_policy:source_code]\\n提交源码",
    )
    monkeypatch.setattr(service, "_locked_item", AsyncMock(return_value=(workflow, stage, item)))

    with pytest.raises(service.GroupWorkflowServiceError) as missing:
        await service.submit_evidence(
            SimpleNamespace(), item_id=item.id, actor_participant_id=actor,
            evidence={"workspace_path": "需求方案/PRD.md", "test_result": "passed"},
        )

    assert missing.value.code == "workflow_source_code_evidence_required"


def test_team_development_stage_assigns_source_code_to_technical_members() -> None:
    from app.services.team_builder.planning import (
        TeamPlanMember,
        TeamPlanWorkflow,
        TeamPlanWorkflowStage,
        team_workflow_to_workflow_plan,
        workflow_plan_requires_source_code,
    )

    leader = TeamPlanMember(
        member_key="leader", name="群主", role_description="项目管理", responsibility="分派工作",
        source="new", is_leader=True,
    )
    engineer = TeamPlanMember(
        member_key="frontend", name="前端工程师", role_description="前端开发", responsibility="实现界面",
        source="new",
    )
    leader_id, engineer_id = uuid.uuid4(), uuid.uuid4()
    workflow = TeamPlanWorkflow(
        preset="custom", name="研发", stages=[
            TeamPlanWorkflowStage(key="build", title="需求开发", goal="完成可运行实现"),
            TeamPlanWorkflowStage(key="accept", title="需求验收", goal="验证实现可运行"),
        ],
    )

    plan = team_workflow_to_workflow_plan(
        workflow,
        goal="交付产品",
        leader_participant_id=leader_id,
        members=[leader, engineer],
        member_participant_ids={"leader": leader_id, "frontend": engineer_id},
    )

    assert workflow_plan_requires_source_code(plan) is True
    assert len(plan.stages[0].items) == 1
    assert plan.stages[0].items[0].assignee_participant_id == engineer_id
    assert "[evidence_policy:source_code]" in plan.stages[0].items[0].description


@pytest.mark.asyncio
async def test_reconcile_stops_at_approval_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), version=4, status="active")
    stage = SimpleNamespace(id=uuid.uuid4(), title="验收", requires_approval=True, status="active")
    item = SimpleNamespace(status="done")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [item]))))
    action = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(service, "_leader_action", AsyncMock(return_value=action))
    monkeypatch.setattr(service, "_refresh_ready_items", AsyncMock(return_value=()))
    monkeypatch.setattr(service, "_decision_action", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_workflow_okr_requires_human_confirm", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_notify_okr", AsyncMock())

    result = await service._reconcile(db, workflow=workflow, stage=stage)

    assert stage.status == "awaiting_approval"
    assert workflow.status == "awaiting_approval"
    assert result.next_stage is None
    assert result.leader_action is action


@pytest.mark.asyncio
async def test_reconcile_does_not_block_on_okr_workflow_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """OKR project push must not invent approval gates on non-approval stages."""
    workflow = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), version=4, status="active", group_id=uuid.uuid4())
    stage = SimpleNamespace(
        id=uuid.uuid4(), title="澄清目标", requires_approval=False, status="active", position=0, completed_at=None
    )
    item = SimpleNamespace(status="done")
    next_stage = SimpleNamespace(id=uuid.uuid4(), position=1, status="pending", started_at=None)
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [item])),
                SimpleNamespace(scalar_one_or_none=lambda: next_stage),
            ]
        )
    )
    action = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(service, "_event", AsyncMock())
    monkeypatch.setattr(service, "_leader_action", AsyncMock(return_value=action))
    monkeypatch.setattr(service, "_refresh_ready_items", AsyncMock(return_value=()))
    monkeypatch.setattr(service, "_workflow_okr_requires_human_confirm", AsyncMock(return_value=True))
    notify = AsyncMock()
    monkeypatch.setattr(service, "_notify_okr", notify)

    result = await service._reconcile(db, workflow=workflow, stage=stage)

    assert stage.status == "completed"
    assert workflow.status == "active"
    assert result.next_stage is next_stage
    assert result.leader_action is action
    assert any(call.args[1] == "stage_completed" for call in notify.await_args_list)


@pytest.mark.asyncio
async def test_complete_stage_okr_only_after_human_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        version=1,
        status="awaiting_approval",
        current_stage_id=None,
    )
    stage = SimpleNamespace(id=uuid.uuid4(), position=0, title="交付", status="awaiting_approval", completed_at=None)
    next_stage = SimpleNamespace(id=uuid.uuid4(), position=1, status="pending", started_at=None)
    action = SimpleNamespace(id=uuid.uuid4())

    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: next_stage))
    )
    monkeypatch.setattr(service, "_event", AsyncMock())
    monkeypatch.setattr(service, "_leader_action", AsyncMock(return_value=action))
    monkeypatch.setattr(service, "_refresh_ready_items", AsyncMock(return_value=()))
    notify = AsyncMock()
    monkeypatch.setattr(service, "_notify_okr", notify)

    await service._complete_stage(db, workflow=workflow, stage=stage, source="workflow")
    assert notify.await_count >= 1
    assert any(call.kwargs.get("confirmed") is True for call in notify.await_args_list)

    notify.reset_mock()
    stage.status = "awaiting_approval"
    await service._complete_stage(db, workflow=workflow, stage=stage, source="human")
    assert any(call.kwargs.get("confirmed") is True for call in notify.await_args_list)
