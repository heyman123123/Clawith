"""Coverage for durable company role-library accumulation."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import company_role_library
from app.services.team_builder import provisioning
from app.services.team_builder.planning import TeamPlan, TeamPlanMember


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def exists(self, key: str) -> bool:
        return key in self.files

    async def write_text(self, key: str, content: str, encoding: str = "utf-8") -> None:
        self.files[key] = content


@pytest.mark.asyncio
async def test_company_role_library_writes_generated_index_without_overwriting_readme(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    builtin = SimpleNamespace(
        id=uuid.uuid4(), name="前端开发者", description="实现 Web 页面", category="engineering",
        capability_bullets=["React"], is_builtin=True,
    )
    custom = SimpleNamespace(
        id=uuid.uuid4(), name="行业研究员", description="研究目标行业", category="company-custom",
        capability_bullets=["竞品"], is_builtin=False, soul_template="# Soul — 行业研究员",
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [builtin, custom])))
    )
    storage = _Storage()
    monkeypatch.setattr(company_role_library, "get_storage_backend", lambda: storage)

    templates = await company_role_library.ensure_company_role_library(db, tenant_id=tenant_id)
    readme_key = f"enterprise_info_{tenant_id}/knowledge_base/role-library/README.md"
    source_key = f"enterprise_info_{tenant_id}/knowledge_base/role-library/SOURCE.md"
    catalog_key = f"enterprise_info_{tenant_id}/knowledge_base/role-library/catalog.generated.json"
    custom_key = f"enterprise_info_{tenant_id}/knowledge_base/role-library/custom/{custom.id}.md"
    assert templates == [builtin, custom]
    assert "默认公司角色库" in storage.files[readme_key]
    assert "agency-agents-zh" in storage.files[source_key]
    assert json.loads(storage.files[catalog_key])["role_count"] == 2
    assert "行业研究员" in storage.files[custom_key]

    storage.files[readme_key] = "公司自行维护的说明"
    await company_role_library.ensure_company_role_library(db, tenant_id=tenant_id)
    assert storage.files[readme_key] == "公司自行维护的说明"


@pytest.mark.asyncio
async def test_provisioning_archives_a_new_role_before_creating_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    role = TeamPlanMember(
        member_key="industry_researcher",
        name="行业研究员",
        role_description="行业和竞品研究",
        responsibility="完成市场、竞品与机会研究并提交证据。",
        source="new",
        is_leader=True,
    )
    plan = TeamPlan(group_name="研究组", goal="研究新行业", assumptions=[], phases=["研究"], members=[role])
    record = SimpleNamespace(
        member_key=role.member_key, agent_id=None, participant_id=None, role_spec={}, status="pending",
        error_code=None, error_message=None,
    )
    template = SimpleNamespace(id=uuid.uuid4())
    agent = SimpleNamespace(id=uuid.uuid4(), status="idle", name=role.name, avatar_url=None)
    participant = SimpleNamespace(id=uuid.uuid4())
    db = SimpleNamespace(flush=AsyncMock())

    monkeypatch.setattr(provisioning, "validate_team_plan", lambda _payload: plan)
    create_template = AsyncMock(return_value=template)
    monkeypatch.setattr(provisioning, "get_or_create_company_role_template", create_template)
    monkeypatch.setattr(provisioning, "_new_agent", AsyncMock(return_value=(agent, participant.id)))
    monkeypatch.setattr(provisioning, "_initialize_new_agent", AsyncMock())

    participant_ids, leader_id, participant_by_member_key = await provisioning._resolve_members(
        db,
        job=SimpleNamespace(tenant_id=tenant_id),
        draft=SimpleNamespace(reviewed_plan={}),
        user=user,
        members=[record],
    )

    create_template.assert_awaited_once_with(
        db,
        tenant_id=tenant_id,
        creator_id=user.id,
        name="行业研究员",
        role_description="行业和竞品研究",
        responsibility="完成市场、竞品与机会研究并提交证据。",
    )
    assert record.role_spec["template_id"] == str(template.id)
    assert record.agent_id == agent.id
    assert participant_ids == [participant.id]
    assert leader_id == participant.id
    assert participant_by_member_key == {role.member_key: participant.id}
