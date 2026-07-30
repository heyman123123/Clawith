# 群协作生命周期工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个群以可审计的阶段、工作项、证据和阻塞来主动推进协作，并提供预设/AI 生成流程及推进指挥台。

**Architecture:** 新增独立的群工作流领域模型与服务，不复用个人 `Task`。群主和成员通过 Runtime 的群专属结构化工具写入工作项事件；服务以事件和证据驱动阶段推进，并用幂等的群主行动命令发起公开协作。群 API 提供读写边界，群侧栏以推进指挥台展示当前状态。

**Tech Stack:** FastAPI、SQLAlchemy async、Alembic、Pydantic、Agent Runtime/LangGraph、React、TanStack Query、TypeScript。

**Constraint:** 不创建、不推送 Git commit；所有命令以 `rtk` 开头。

---

## 文件结构

- Create: `backend/app/models/group_workflow.py` — 工作流、阶段、工作项、事件和 AI 草案 ORM。
- Create: `backend/app/services/group_workflow/contracts.py` — 模板/AI 草案的严格 Pydantic 合约。
- Create: `backend/app/services/group_workflow/templates.py` — 默认、敏捷需求、产研协作模板。
- Create: `backend/app/services/group_workflow/service.py` — 事务内状态转换、证据校验、幂等事件与群主行动。
- Create: `backend/app/services/group_workflow/planning.py` — AI 草案生成及有限群上下文快照。
- Create: `backend/app/services/group_workflow/worker.py` — 恢复待派发群主行动，但不按时间制造新动作。
- Create: `backend/app/api/group_workflows.py` — `/api/groups/{group_id}/workflow` 边界。
- Create: `backend/alembic/versions/202607301500_add_group_workflows.py` — 新表、约束和索引。
- Create: `frontend/src/pages/groups/GroupWorkflowTab.tsx` — 推进指挥台。
- Create: `frontend/src/pages/groups/WorkflowManageModal.tsx` — 模板切换、AI 草案和关键关口确认。
- Create: `frontend/src/services/groupWorkflowApi.ts` and `frontend/src/types/groupWorkflow.ts` — 前端 API 与类型。
- Modify: `backend/app/alembic/env.py`, `backend/app/main.py`, `backend/app/services/group_chat_service.py`, `backend/app/services/team_builder/provisioning.py`, `backend/app/services/agent_runtime/group_runtime_tools.py`, `backend/app/services/agent_runtime/group_context_builder.py`, `backend/app/services/builtin_tool_definitions.py`, `backend/app/services/group_realtime.py`, `backend/app/api/groups.py`, `frontend/src/pages/groups/GroupSidePanel.tsx`, `frontend/src/pages/groups/GroupsPage.tsx`, `frontend/src/pages/groups/groups.css`, `frontend/src/i18n/zh.json`, `frontend/src/i18n/en.json`。

## Task 1: 持久化工作流领域模型

**Files:**
- Create: `backend/app/models/group_workflow.py`
- Create: `backend/alembic/versions/202607301500_add_group_workflows.py`
- Modify: `backend/alembic/env.py`
- Test: `backend/tests/test_group_workflow_migration.py`

- [ ] **Step 1: 写失败的迁移和合约测试**

```python
def test_workflow_models_have_one_active_group_scope_and_versioned_events() -> None:
    assert GroupWorkflow.__tablename__ == "group_workflows"
    assert "uq_group_workflows_group" in {item.name for item in GroupWorkflow.__table__.constraints}
    assert GroupWorkflowEvent.__tablename__ == "group_workflow_events"

def test_migration_depends_on_ai_interaction_times() -> None:
    assert migration.down_revision == "ai_interaction_times"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_migration.py -q`

- [ ] **Step 3: 定义模型和迁移**

`GroupWorkflow` 以 `group_id` 唯一关联群，字段含 `source` (`default|agile|product_research|ai`)、`status` (`active|paused|awaiting_approval|completed`)、`current_stage_id` 和 `version`。阶段以 `(workflow_id, position)` 唯一；工作项以 `stage_id` 关联、保存 `assignee_participant_id`、`status` (`pending|in_progress|blocked|awaiting_approval|done`)、`evidence` JSONB、`blocked_reason` 和 `version`；事件保存 `event_type`、`actor_participant_id`、`source`、`payload`、唯一 `idempotency_key`、`dispatch_state` (`pending|claimed|dispatched`) 和 `dispatched_at`。草案保存 `request`, `plan`, `status`, `error_*` 与 `confirmed_at`。

迁移创建外键、检查约束和索引：`workflow/group`、`stage/workflow/position`、`item/stage/status`、`event/workflow/created_at`、`draft/group/created_at`。所有 UUID 指向现有 `groups`、`participants` 与 `users` 表，删除群时级联删除。

- [ ] **Step 4: 运行迁移测试及 schema 检查**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_migration.py -q && DEBUG=false rtk uv run --extra dev ruff check app/models/group_workflow.py alembic/versions/202607301500_add_group_workflows.py`

## Task 2: 模板与 AI 输出合约

**Files:**
- Create: `backend/app/services/group_workflow/contracts.py`
- Create: `backend/app/services/group_workflow/templates.py`
- Test: `backend/tests/test_group_workflow_templates.py`

- [ ] **Step 1: 写失败的模板测试**

```python
def test_agile_template_has_ordered_gated_delivery_stages() -> None:
    plan = preset_workflow("agile", goal="发布搜索")
    assert [stage.key for stage in plan.stages] == ["clarify", "backlog", "plan", "build", "accept", "retro"]
    assert plan.stages[4].requires_approval is True

def test_plan_rejects_duplicate_stage_keys_and_unknown_assignees() -> None:
    with pytest.raises(GroupWorkflowPlanError):
        validate_workflow_plan({"stages": [{"key": "same"}, {"key": "same"}]})
```

- [ ] **Step 2: 实现严格计划模型**

定义 `WorkflowPlan`, `WorkflowStagePlan`, `WorkflowItemPlan`。阶段 key 必须唯一、有顺序，工作项必须引用同一计划阶段和群内 participant；`requires_approval` 仅允许验收/发布型关口。实现默认协作推进、`agile` 与 `product_research` 模板，且模板都包含群主作为编排者和至少一个可完成工作项。

- [ ] **Step 3: 运行模板测试**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_templates.py -q`

## Task 3: 状态转换、证据推进与群主行动

**Files:**
- Create: `backend/app/services/group_workflow/service.py`
- Test: `backend/tests/test_group_workflow_service.py`

- [ ] **Step 1: 写失败的状态转换测试**

```python
async def test_evidence_completes_automatic_stage_once() -> None:
    result = await service.submit_evidence(db, item_id=item.id, actor=agent, evidence={"ref": "TEAM_BRIEF.md"})
    assert result.stage.status == "completed"
    assert result.next_stage.status == "active"
    assert result.leader_action.kind == "stage_advanced"

async def test_approval_gate_never_auto_advances() -> None:
    result = await service.submit_evidence(db, item_id=acceptance_item.id, actor=agent, evidence={"ref": "test-report"})
    assert result.stage.status == "awaiting_approval"
    assert result.next_stage is None
```

- [ ] **Step 2: 实现事务服务**

实现 `create_default_workflow`, `replace_workflow_from_plan`, `start_item`, `submit_evidence`, `set_blocked`, `clear_blocked`, `confirm_stage`, `pause`, `resume`。每个方法锁定 workflow/item，验证 actor 的群成员资格和版本，写入不可变 event。`_reconcile` 仅在证据完整时推进；审批关口只写 `awaiting_approval`。`_leader_action` 以 `workflow.version + actionable_state` 生成唯一键，写入一个 `dispatch_state=pending` 的行动事件，避免重复提醒。

- [ ] **Step 3: 运行服务测试**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_service.py -q`

## Task 4: 群创建默认流程和 AI 草案

**Files:**
- Create: `backend/app/services/group_workflow/planning.py`
- Modify: `backend/app/services/group_chat_service.py`
- Modify: `backend/app/services/team_builder/provisioning.py`
- Test: `backend/tests/test_group_workflow_planning.py`

- [ ] **Step 1: 写失败的创建/草案测试**

```python
async def test_group_creation_stages_default_workflow(db) -> None:
    group = await create_group(...)
    assert (await service.get_current(db, group.id)).source == "default"

async def test_ai_draft_does_not_replace_active_workflow_before_confirmation(monkeypatch) -> None:
    draft = await planning.generate_draft(...)
    assert draft.status == "ready"
    assert current_workflow.id != draft.workflow_id
```

- [ ] **Step 2: 创建默认流程并实现 AI 草案**

在 `create_group` 成功落库同一事务中调用 `create_default_workflow`；团队搭建流程创建群后使用已确认团队目标和群主身份填充默认流程。已有群由 Task 5 的首次 `GET workflow` 在同一授权事务中懒创建默认流程，不解析任何历史消息。AI 生成仿照 `team_builder/planning.py`：读取公告、成员和最多 20 个受限大小的工作区文本摘要，调用平台规划模型，使用 `ai_interaction_scope` 绑定 tenant/group session，严格解析为 `WorkflowPlan`，仅创建 `GroupWorkflowDraft`。确认草案时通过 `replace_workflow_from_plan` 原子替换未完成流程。

- [ ] **Step 3: 运行计划测试**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_planning.py -q`

## Task 5: 群 API、权限、分页和实时事件

**Files:**
- Create: `backend/app/api/group_workflows.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/group_realtime.py`
- Test: `backend/tests/test_group_workflow_api.py`

- [ ] **Step 1: 写失败的 API 边界测试**

```python
def test_workflow_routes_precede_dynamic_group_route() -> None:
    assert ("GET", "/api/groups/{group_id}/workflow") in routes
    assert ("POST", "/api/groups/{group_id}/workflow/drafts") in routes

async def test_member_cannot_confirm_gate(monkeypatch) -> None:
    with pytest.raises(HTTPException) as exc:
        await confirm_stage(...)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: 实现 API**

暴露 `GET workflow`、`GET events?page&page_size`、`POST preset`、`POST drafts`、`GET drafts/{id}`、`POST drafts/{id}/confirm`、`PATCH items/{id}`、`POST items/{id}/evidence`、`POST items/{id}/block`、`POST stages/{id}/confirm`、`POST pause` 和 `POST resume`。首次读取已有群时在同一授权事务中懒创建默认流程。读取允许活跃人类成员；更新自身工作项允许被指派成员或群主；模板、草案、暂停、恢复和验收确认只允许群管理者。响应同时返回 workflow snapshot、分页 event metadata 和 `leader_next_action`。

每次提交后调用 `publish_group_workflow_changed(group_id, workflow_id, version)`；新增 websocket 消息类型 `workflow.changed`，前端按 query key 失效。

- [ ] **Step 3: 运行 API 测试**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_api.py tests/test_group_api.py -q`

## Task 6: Runtime 工具、上下文与状态驱动群主唤醒

**Files:**
- Modify: `backend/app/services/builtin_tool_definitions.py`
- Modify: `backend/app/services/agent_runtime/group_runtime_tools.py`
- Modify: `backend/app/services/agent_runtime/group_context_builder.py`
- Create: `backend/app/services/group_workflow/worker.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_group_workflow_runtime.py`

- [ ] **Step 1: 写失败的 Runtime 测试**

```python
async def test_group_agent_can_submit_evidence_only_for_its_assigned_item() -> None:
    outcome = await runtime_tools.execute(..., tool_name="group_workflow_submit_evidence", arguments={"item_id": str(item.id), "evidence": {"ref": "report.md"}})
    assert outcome.status == "succeeded"

async def test_same_actionable_state_enqueues_one_leader_activation() -> None:
    await worker.dispatch_leader_actions_once()
    await worker.dispatch_leader_actions_once()
    assert enqueue_group_message.await_count == 1
```

- [ ] **Step 2: 实现群专属工具与上下文**

在 `GROUP_RUNTIME_TOOL_DEFINITIONS` 和 `GROUP_TOOL_NAMES` 添加 `group_workflow_read`, `group_workflow_start_item`, `group_workflow_submit_evidence`, `group_workflow_block_item`, `group_workflow_unblock_item`, `group_workflow_request_approval`。工具执行调用 Task 3 服务，始终从 `_scope` 获取 group/session/agent identity；不接受客户端传入 participant。`GroupContextBuilder.capture` 冻结当前阶段、阻塞、受该 agent 指派的工作项及群主下一步，并在群主 prompt 中要求公开分发而不是等待定时轮询。

实现 worker 以 `FOR UPDATE SKIP LOCKED` 只领取 `event_type=leader_action AND dispatch_state=pending` 的持久化动作，调用现有 `enqueue_group_message` @群主，随后将该事件标记 `dispatched` 并记录时间。worker 在 `main.py` 的 worker role 启动；轮询仅恢复未派发动作，不创建新动作。

- [ ] **Step 3: 运行 Runtime 测试**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_runtime.py tests/test_group_message_service.py -q`

## Task 7: 推进指挥台与管理面板

**Files:**
- Create: `frontend/src/types/groupWorkflow.ts`
- Create: `frontend/src/services/groupWorkflowApi.ts`
- Create: `frontend/src/pages/groups/GroupWorkflowTab.tsx`
- Create: `frontend/src/pages/groups/WorkflowManageModal.tsx`
- Modify: `frontend/src/pages/groups/GroupSidePanel.tsx`
- Modify: `frontend/src/pages/groups/GroupsPage.tsx`
- Modify: `frontend/src/pages/groups/groups.css`
- Modify: `frontend/src/i18n/zh.json`
- Modify: `frontend/src/i18n/en.json`
- Test: `frontend/tests/groupWorkflowContract.test.mjs`

- [ ] **Step 1: 写前端契约测试**

```js
assert.match(panel, /leader_next_action/);
assert.match(panel, /awaiting_approval/);
assert.match(sidePanel, /key: 'workflow'/);
assert.match(api, /\/groups\/\$\{groupId\}\/workflow/);
```

- [ ] **Step 2: 实现 API 类型和 Query**

定义 `GroupWorkflow`, `WorkflowStage`, `WorkflowItem`, `WorkflowEvent`, `WorkflowDraft` 与分页响应。为 snapshot、事件页和草案使用独立 TanStack Query keys；收到 `workflow.changed` 时失效当前群 workflow query。API 客户端向 Task 5 的精确路径发送 `expected_version`，冲突时刷新 snapshot 并显示当前状态。

- [ ] **Step 3: 实现指挥台与管理 Modal**

在“公告”左侧插入 `workflow` tab。顶部显示来源、总进度、当前阶段和健康状态；主体按状态分组工作项，突出阶段门槛、证据、阻塞和 `leader_next_action`。管理弹窗提供模板单选、AI 草案生成/加载/预览/确认、暂停/恢复、验收确认。普通成员隐藏管理按钮；任务卡只向其负责人显示更新动作。

- [ ] **Step 4: 实现样式和响应式约束**

在 `groups.css` 使用现有 CSS 变量，实现紧凑指挥台、阶段轨迹、健康状态、阻塞提示和工作项队列；在窄侧栏保持横向阶段轨迹可滚动，详情区纵向滚动。保持群主成员卡在任何成员区首位。

- [ ] **Step 5: 运行前端验证**

Run: `cd frontend && rtk node --test tests/groupWorkflowContract.test.mjs tests/teamBuilderContract.test.mjs && rtk npm run build`

## Task 8: 端到端回归与迁移验证

**Files:**
- Modify: `backend/tests/test_group_workflow_migration.py`
- Modify: `backend/tests/test_group_workflow_api.py`
- Modify: `frontend/tests/groupWorkflowContract.test.mjs`

- [ ] **Step 1: 添加覆盖完整生命周期的回归测试**

```python
async def test_default_workflow_progresses_from_evidence_to_approval_then_completion() -> None:
    workflow = await service.create_default_workflow(...)
    await service.submit_evidence(...)
    assert workflow.current_stage.status == "awaiting_approval"
    await service.confirm_stage(...)
    assert workflow.current_stage.position == 1
```

- [ ] **Step 2: 运行完整定向套件**

Run: `cd backend && DEBUG=false rtk uv run --extra dev pytest tests/test_group_workflow_migration.py tests/test_group_workflow_templates.py tests/test_group_workflow_service.py tests/test_group_workflow_planning.py tests/test_group_workflow_api.py tests/test_group_workflow_runtime.py tests/test_group_api.py -q && DEBUG=false rtk uv run --extra dev ruff check app/models/group_workflow.py app/services/group_workflow app/api/group_workflows.py`

- [ ] **Step 3: 校验迁移和容器**

Run: `rtk docker compose up -d --build backend frontend && rtk docker compose exec -T backend alembic current`

Expected: `group_workflows` migration is `head`; backend health check succeeds; frontend build completes.

## 自检

- 规格中的默认、敏捷、产研和 AI 草案路径分别由 Tasks 2 和 4 覆盖。
- 结构化证据、审批关口、幂等群主行动和恢复 worker 分别由 Tasks 3 与 6 覆盖。
- 工作流面板、管理功能、角色权限、分页和实时更新分别由 Tasks 5 与 7 覆盖。
- 没有将普通聊天文本当作完成证据，也没有修改个人 Task/OKR 体系。
