"""HR proposal persistence and select fallback tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.chat_session import ChatSession
from app.models.group import Group
from app.models.hr_review import HrReviewSession
from app.services.hr_review_board_seeder import HR_REVIEW_BOARD_GROUP_TYPE
from app.services.hr_review_session_service import (
    HrReviewError,
    extract_hr_proposals_from_text,
    process_hr_group_agent_output,
    select_proposal,
    sync_hr_context_from_user_message,
)


def _role(**overrides):
    base = {
        "key": "pm",
        "name": "项目经理",
        "duties": "统筹交付",
        "soul": "# PM\nYou lead the group.",
        "is_group_leader": True,
        "suggested_tools": ["group_write_workspace_file"],
        "suggested_permissions": {"scope_type": "company", "access_level": "use"},
    }
    base.update(overrides)
    return base


def _proposals(count: int = 3) -> list[dict]:
    return [
        {
            "id": f"proposal_{index}",
            "label": f"方案 {index}",
            "card_summary": f"摘要 {index}",
            "roles": [
                _role(key=f"lead_{index}", name=f"Lead {index}", is_group_leader=True),
                _role(key=f"dev_{index}", name=f"Dev {index}", is_group_leader=False),
            ],
        }
        for index in range(1, count + 1)
    ]


def test_extract_hr_proposals_from_fenced_json():
    proposals = _proposals()
    text = f"请确认方案：\n```json\n{json.dumps({'proposals': proposals}, ensure_ascii=False)}\n```"
    parsed = extract_hr_proposals_from_text(text)
    assert parsed is not None
    assert len(parsed["proposals"]) == 3


def test_extract_hr_proposals_reads_session_marker():
    hr_session_id = uuid.uuid4()
    proposals = _proposals()
    text = (
        f"<!--hr_review_session:{hr_session_id}-->\n"
        f"```json\n{json.dumps({'proposals': proposals}, ensure_ascii=False)}\n```"
    )
    parsed = extract_hr_proposals_from_text(text)
    assert parsed is not None
    assert parsed["hr_session_id"] == str(hr_session_id)


@pytest.mark.asyncio
async def test_process_hr_group_agent_output_persists_proposals() -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    chat_session_id = uuid.uuid4()
    hr_session_id = uuid.uuid4()
    hr_session = HrReviewSession(
        id=hr_session_id,
        group_id=group_id,
        session_id=chat_session_id,
        session_type="team_building",
        status="open",
        proposals=[],
        context_payload={},
        created_at=datetime.now(UTC),
    )
    group = Group(
        id=group_id,
        tenant_id=tenant_id,
        name="HR 评审群",
        description=None,
        group_type=HR_REVIEW_BOARD_GROUP_TYPE,
        created_by_participant_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    class _DB:
        async def scalar(self, _query):
            return hr_session

        async def get(self, model, key):
            if model is HrReviewSession and key == hr_session_id:
                return hr_session
            if model is Group and key == group_id:
                return group
            return None

        async def flush(self):
            return None

    proposals = _proposals()
    text = f"```json\n{json.dumps({'proposals': proposals}, ensure_ascii=False)}\n```"
    result = await process_hr_group_agent_output(
        _DB(),
        tenant_id=tenant_id,
        chat_session_id=chat_session_id,
        text=text,
    )
    assert result is hr_session
    assert len(hr_session.proposals) == 3
    assert hr_session.proposals[0]["id"] == "proposal_1"


@pytest.mark.asyncio
async def test_sync_hr_context_from_user_message_fills_requirements() -> None:
    tenant_id = uuid.uuid4()
    chat_session_id = uuid.uuid4()
    hr_session = HrReviewSession(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        session_id=chat_session_id,
        session_type="team_building",
        status="open",
        proposals=[],
        context_payload={"name": "新需求"},
        created_at=datetime.now(UTC),
    )

    class _DB:
        async def scalar(self, _query):
            return hr_session

        async def get(self, _model, _key):
            return None

        async def flush(self):
            return None

    await sync_hr_context_from_user_message(
        _DB(),
        tenant_id=tenant_id,
        chat_session_id=chat_session_id,
        content="Build a mobile app for inventory tracking",
    )
    assert hr_session.context_payload["requirements"] == "Build a mobile app for inventory tracking"


@pytest.mark.asyncio
async def test_select_proposal_uses_fallback_proposals_when_db_empty(monkeypatch) -> None:
    hr_session_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        display_name="Owner",
        avatar_url=None,
    )
    proposals = _proposals()
    hr_session = SimpleNamespace(
        id=hr_session_id,
        session_type="team_building",
        status="open",
        group_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        proposals=[],
        context_payload={"name": "Demo", "requirements": "Ship MVP"},
        selected_proposal_id=None,
        closed_at=None,
    )
    attach_calls: list[list] = []

    async def _attach_proposals(_db, *, hr_session_id, proposals):
        attach_calls.append(proposals)
        hr_session.proposals = proposals
        return hr_session

    class _Session:
        async def get(self, _model, _id):
            return hr_session

        async def flush(self):
            return None

    monkeypatch.setattr(
        "app.services.hr_review_session_service.get_hr_session_for_tenant",
        AsyncMock(return_value=hr_session),
    )
    monkeypatch.setattr(
        "app.services.hr_review_session_service.attach_proposals",
        _attach_proposals,
    )
    monkeypatch.setattr(
        "app.services.hr_review_session_service._send_hr_selection_receipt",
        AsyncMock(),
    )
    provisioned = {
        "roles": proposals[0]["roles"],
        "wake_up_message": "@项目经理 kickoff",
        "project_name": "Demo",
        "requirements": "Ship MVP",
        "workflow_id": str(uuid.uuid4()),
        "group_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
    }
    import sys

    monkeypatch.setitem(
        sys.modules,
        "app.services.project_provisioning",
        SimpleNamespace(
            ProjectProvisioningError=RuntimeError,
            provision_team_from_plan=AsyncMock(return_value=provisioned),
        ),
    )

    await select_proposal(
        _Session(),
        hr_session_id=hr_session_id,
        proposal_id="proposal_1",
        user=user,
        fallback_proposals=proposals,
    )
    assert len(attach_calls) == 1
    assert len(attach_calls[0]) == 3
    assert hr_session.status == "completed"


@pytest.mark.asyncio
async def test_select_proposal_rejects_non_open_session(monkeypatch) -> None:
    hr_session = SimpleNamespace(
        id=uuid.uuid4(),
        session_type="team_building",
        status="completed",
        proposals=_proposals(),
        context_payload={},
    )
    monkeypatch.setattr(
        "app.services.hr_review_session_service.get_hr_session_for_tenant",
        AsyncMock(return_value=hr_session),
    )
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), display_name="U", avatar_url=None)
    with pytest.raises(HrReviewError, match="已选择或完成"):
        await select_proposal(
            object(),
            hr_session_id=hr_session.id,
            proposal_id="proposal_1",
            user=user,
        )
