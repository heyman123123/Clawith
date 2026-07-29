from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import project_kickoff_service
from app.services.hr_review_session_service import select_proposal
from app.services.project_provisioning import provision_team_from_plan


def test_provision_team_from_plan_accepts_send_kickoff_kwarg():
    params = inspect.signature(provision_team_from_plan).parameters
    assert "send_kickoff" in params
    assert params["send_kickoff"].default is True


def test_select_proposal_accepts_send_kickoff_kwarg():
    params = inspect.signature(select_proposal).parameters
    assert "send_kickoff" in params
    assert params["send_kickoff"].default is True


@pytest.mark.asyncio
async def test_draft_kickoff_falls_back_to_template_when_llm_fails():
    workflow_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    leader_participant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()

    workflow = SimpleNamespace(
        id=workflow_id,
        tenant_id=tenant_id,
        name="跨境店",
        requirements="做 Shopify 一件代发",
        status="active",
        team_plan={
            "project_name": "跨境店",
            "requirements": "做 Shopify 一件代发",
            "roles": [
                {
                    "key": "leader",
                    "name": "运营负责人",
                    "duties": "统筹",
                    "soul": "# L",
                    "is_group_leader": True,
                    "suggested_tools": [],
                    "suggested_permissions": {"scope_type": "company", "access_level": "use"},
                },
                {
                    "key": "ops",
                    "name": "选品专员",
                    "duties": "选品",
                    "soul": "# O",
                    "is_group_leader": False,
                    "suggested_tools": [],
                    "suggested_permissions": {"scope_type": "company", "access_level": "use"},
                },
            ],
        },
        group_id=group_id,
        group_leader_agent_id=uuid.uuid4(),
        kickoff_sent_at=None,
    )

    with (
        patch.object(project_kickoff_service, "_load_workflow", AsyncMock(return_value=workflow)),
        patch.object(
            project_kickoff_service,
            "_resolve_execution_context",
            AsyncMock(
                return_value={
                    "group_id": group_id,
                    "session_id": session_id,
                    "leader_participant_id": leader_participant_id,
                    "leader_name": "运营负责人",
                }
            ),
        ),
        patch.object(
            project_kickoff_service,
            "_llm_draft_kickoff",
            AsyncMock(side_effect=RuntimeError("llm down")),
        ),
    ):
        result = await project_kickoff_service.draft_kickoff_message(
            AsyncMock(),
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
        )

    assert result["leader_name"] == "运营负责人"
    assert "请现在启动团队" in result["content"]
    assert str(result["group_id"]) == str(group_id)


@pytest.mark.asyncio
async def test_send_kickoff_is_idempotent_when_already_sent():
    workflow_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    workflow = SimpleNamespace(
        id=workflow_id,
        tenant_id=tenant_id,
        status="active",
        group_id=uuid.uuid4(),
        kickoff_sent_at=datetime.now(UTC),
    )
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, display_name="Me", avatar_url=None)

    with (
        patch.object(project_kickoff_service, "_load_workflow", AsyncMock(return_value=workflow)),
        patch.object(
            project_kickoff_service,
            "_resolve_execution_context",
            AsyncMock(
                return_value={
                    "group_id": workflow.group_id,
                    "session_id": uuid.uuid4(),
                    "leader_participant_id": uuid.uuid4(),
                    "leader_name": "运营负责人",
                }
            ),
        ),
    ):
        result = await project_kickoff_service.send_kickoff_message(
            AsyncMock(),
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            user=user,
            content="@运营负责人 开工",
        )

    assert result["already_sent"] is True
