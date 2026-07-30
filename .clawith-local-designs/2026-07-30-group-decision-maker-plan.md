# 群决策者（Decision Maker）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个执行群绑定专属决策者 Agent：常规项目决策由其拍板推进，例外私聊人类 manager 求批，拍板后向可配置对象发简短私聊汇报。

**Architecture:** 扩展 `Group`（`decision_maker_participant_id` + `decision_report_participant_ids`）与 `GroupDecisionRequest`；建群/团队 seed 决策者；`approval_required` 时并行唤醒决策者；工具完成分类/确认/求批/汇报；人类 manager 手动确认保留。

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, group workflow worker, agent runtime tools, React GroupSettingsModal

**Spec:** `.clawith-local-designs/2026-07-30-group-decision-maker-design.md`

## Global Constraints

- 决策者与群主分离；群主不得 `confirm_stage`
- 例外类别固定：`human_comms` / `external_deploy` / `finance`；`uncertain` 一律升级
- 例外求批：全部人类 manager，**任一**确认即可
- 汇报对象：`decision_report_participant_ids` 为 `null` → 全部人类 manager；`[]` → 不发；显式列表 → 仅这些人
- 每次决策终态（`auto_applied` / `approved` / `rejected`）必须尝试发汇报
- 无决策者的旧群行为不变（仅人类 manager 确认）
- 决策者禁止自动执行对外部署/打款/代替人类对外沟通
- 私聊失败、汇报失败只记日志，不回滚已生效的阶段确认

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/models/group.py` | 群字段 |
| `backend/app/models/group_decision.py` | `GroupDecisionRequest` 模型（新建） |
| `backend/app/models/__init__.py` | 导出模型 |
| `backend/alembic/versions/202607302300_group_decision_maker.py` | 迁移 |
| `backend/app/services/group_decision/service.py` | 创建请求、批准/拒绝、解析汇报对象、发汇报 |
| `backend/app/services/group_decision/seed.py` | seed 决策者 Agent + 绑定 |
| `backend/app/services/group_decision/wake.py` | 组装决策者唤醒文案 |
| `backend/app/services/group_chat_service.py` | `create_group` / `update_group` 支持决策者与汇报配置 |
| `backend/app/services/team_builder/provisioning.py` | 建团队时 seed+绑定决策者 |
| `backend/app/services/group_workflow/service.py` | `approval_required` 时写 `decision_action`；`confirm_stage` 允许决策者 actor |
| `backend/app/services/group_workflow/worker.py` | 分发 `decision_action`（可复用 leader 分发模式） |
| `backend/app/services/agent_runtime/group_runtime_tools.py` | 决策者工具：分类记录、确认阶段、求批、汇报 |
| `backend/app/services/agent_runtime/group_context_builder.py` | 注入决策者 SOP / 角色标识 |
| `backend/app/api/groups.py` | schema 暴露与 patch |
| `backend/app/api/group_decisions.py` | manager 批准/拒绝 API（新建） |
| `backend/app/main.py` | 挂载 router |
| `frontend/src/types/group.ts` | 类型 |
| `frontend/src/services/groupApi.ts` | API |
| `frontend/src/pages/groups/GroupSettingsModal.tsx` | 改绑决策者、配置汇报对象 |
| `frontend/src/i18n/*` | 文案 |
| `backend/tests/test_group_decision_*.py` | 单测 |

---

### Task 1: 模型 + 迁移

**Files:**
- Create: `backend/app/models/group_decision.py`
- Modify: `backend/app/models/group.py`, `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/202607302300_group_decision_maker.py`
- Test: `backend/tests/test_group_decision_model.py`（或并入现有 model/migration smoke）

- [ ] **Step 1:** 在 `Group` 增加：

```python
decision_maker_participant_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("participants.id", name="fk_groups_decision_maker_participant_id_participants", ondelete="RESTRICT"),
    nullable=True,
)
decision_report_participant_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
# null = default all human managers; [] = no reports; list[str UUID] = explicit
```

- [ ] **Step 2:** 新建 `GroupDecisionRequest`（字段对齐设计文档：`category` / `status` / `title` / `summary` / `recommendation` / `options_json` / `approver_participant_id` / `decided_at` / `report_sent_at` 等），status check：`pending_owner_confirm|approved|rejected|auto_applied|cancelled`；category check：`routine|human_comms|external_deploy|finance|uncertain`。

- [ ] **Step 3:** Alembic 迁移（可空 FK + JSONB + 新表 + 索引 `ix_group_decision_requests_group_status`）。

- [ ] **Step 4:** `alembic upgrade head` 在本地验证；提交。

```bash
cd backend && alembic upgrade head
git add backend/app/models backend/alembic/versions/202607302300_group_decision_maker.py
git commit -m "feat(groups): add decision maker fields and decision requests"
```

---

### Task 2: 决策服务核心（创建 / 批准 / 汇报对象 / 汇报）

**Files:**
- Create: `backend/app/services/group_decision/__init__.py`, `service.py`
- Test: `backend/tests/test_group_decision_service.py`

**Produces:**
- `resolve_report_recipients(db, group) -> list[uuid.UUID]`
- `create_decision_request(...)` / `apply_routine_decision(...)` / `request_owner_confirm(...)`
- `approve_decision(...)` / `reject_decision(...)`（校验 actor 为该群人类 manager）
- `send_decision_report(...)`（direct 私聊；写 `report_sent_at`）

- [ ] **Step 1:** 写失败测试：`null` 汇报配置返回全部人类 manager participant；`[]` 返回空；显式列表过滤非成员。

- [ ] **Step 2:** 实现 `resolve_report_recipients`：查 `GroupMember` + `Participant.type == "user"` + `role == "manager"` + `removed_at is None`。

- [ ] **Step 3:** 写失败测试：`apply_routine` → status `auto_applied` 且调用汇报；`request_owner_confirm` → `pending_owner_confirm` 且不推进阶段；`approve` 任一 manager 成功，第二人再批失败；`reject` 发汇报且不调用 confirm。

- [ ] **Step 4:** 实现服务。汇报文案模板固定短格式：

```text
【决策汇报】{title}
结论：{conclusion}
类别：{category}
依据：{summary 截断}
阶段：{stage_title or "-"}
人类确认：是/否
```

私聊：对每个 recipient 用决策者 agent → 该 user 的 `ensure_primary_direct_session`，写入一条 user-visible 消息（复用现有 direct 入站/通知路径；若已有 agent→user 发送 helper 则复用，否则在 `group_decision/service.py` 内封装 `_dm_user`）。失败 catch+log。

- [ ] **Step 5:** `pytest backend/tests/test_group_decision_service.py -v` 通过后提交。

```bash
git commit -m "feat(groups): decision request service with report DM"
```

---

### Task 3: Seed 决策者 + 建群/团队绑定

**Files:**
- Create: `backend/app/services/group_decision/seed.py`
- Modify: `backend/app/services/group_chat_service.py`（`create_group` 可选 auto-seed 或由调用方传入）
- Modify: `backend/app/services/team_builder/provisioning.py`
- Modify: `backend/app/api/groups.py`（CreateGroup / 手动建群路径）
- Test: `backend/tests/test_group_decision_seed.py`

**Produces:**
- `async def ensure_group_decision_maker(db, *, tenant_id, group, creator_user, goal: str | None) -> uuid.UUID`

- [ ] **Step 1:** seed Agent：`name` 默认「决策者」；personality 强调：代用户拍板；三类例外+uncertain 升级；禁止对外动作；每次拍板后汇报；群主负责执行。

- [ ] **Step 2:** 加入群为 `member`（非 manager），设置 `group.decision_maker_participant_id`；若已有则跳过创建。

- [ ] **Step 3:** `team_builder/provisioning` 在 `create_group` 成功后调用 `ensure_group_decision_maker`。

- [ ] **Step 4:** 手动 `POST /groups`：若未传 `decision_maker_participant_id`，创建后同样 ensure（与设计「建群自动创建」一致）。若显式传入则校验 Agent + 邀请入群 + 绑定。

- [ ] **Step 5:** 测试：provisioning / create_group 后字段非空且 participant 在群内；重复 ensure 幂等。提交。

```bash
git commit -m "feat(groups): auto-seed decision maker on group create"
```

---

### Task 4: 改绑 + 汇报对象 API/Schema

**Files:**
- Modify: `backend/app/api/groups.py`（`GroupOut`, `PatchGroupIn`, `patch_group`）
- Modify: `backend/app/services/group_chat_service.py`（`update_group`）
- Modify: `frontend/src/types/group.ts`, `frontend/src/services/groupApi.ts`
- Test: `backend/tests/test_group_api.py` 增补

- [ ] **Step 1:** `PatchGroupIn` 增加可选：
  - `decision_maker_participant_id: uuid | None`（改绑；`null` 显式清空用 sentinel 或单独 `clear_decision_maker: bool`——推荐：未传不改；传 UUID 改绑）
  - `decision_report_participant_ids: list[uuid] | None`（用三态：字段缺省不改；传 `null` 重置默认；传 `[]` 关闭；传列表设置——Pydantic 可用 `Unset`/`model_fields_set`）

- [ ] **Step 2:** 改绑校验：目标为同租户 `agent` participant；不在群则 `invite` 为 member；不得把群主角色搞乱（决策者保持 member）。

- [ ] **Step 3:** `GroupOut` 返回两字段。前端类型同步。

- [ ] **Step 4:** API 测试 round-trip。提交。

```bash
git commit -m "feat(groups): API to rebind decision maker and report recipients"
```

---

### Task 5: 工作流唤醒决策者 + confirm 授权

**Files:**
- Modify: `backend/app/services/group_workflow/service.py`
- Modify: `backend/app/services/group_workflow/worker.py`
- Create: `backend/app/services/group_decision/wake.py`
- Test: `backend/tests/test_group_workflow_decision_wake.py`, 扩展 `test_group_workflow_service.py`

- [ ] **Step 1:** 在进入 `awaiting_approval` 且已 `_leader_action(..., kind="approval_required")` 之后，若 `group.decision_maker_participant_id` 存在，再写事件：
  - `event_type="decision_action"`（或 payload.kind + 复用 leader_action 但 target 为决策者——**推荐独立 `decision_action`**，避免群主文案串台）
  - payload：`kind=approval_required`, `stage_id`, `stage_title`, `workflow_id`, `group_id`

- [ ] **Step 2:** worker 增加 `dispatch_decision_actions_once`（镜像 leader 分发）：@决策者，文案来自 `build_decision_wake_content`——要求分类并调用工具；常规则确认；例外则私聊 manager。

- [ ] **Step 3:** 扩展 `confirm_stage`（或新增 `confirm_stage_by_decision_maker`）：
  - HTTP API 仍仅 `manager=True` 人类
  - 内部服务允许 `actor_participant_id == group.decision_maker_participant_id`；`source` 记为 `"decision_maker"`（OKR `confirmed` 视为已确认到达，与 human 同等）
  - 若 actor 是群主且非决策者 → 拒绝

- [ ] **Step 4:** 测试：有决策者时 approval 产生两条 pending 事件；confirm 来自决策者成功、来自 leader 失败、来自人类 manager 成功。提交。

```bash
git commit -m "feat(workflow): wake decision maker and allow decision confirm"
```

---

### Task 6: Runtime 工具 + Context SOP

**Files:**
- Modify: `backend/app/services/agent_runtime/group_runtime_tools.py`
- Modify: `backend/app/services/agent_runtime/group_context_builder.py`
- Test: `backend/tests/test_group_decision_tools.py`（可 mock service）

**工具（仅决策者可用）：**
- `group_decision_classify_and_act`：入参 `category`, `title`, `summary`, `recommendation`, `stage_id?`；routine → apply+confirm+report；例外 → request_owner_confirm+DM managers
- `group_decision_confirm_stage`：仅 routine/已批准后的显式确认（若上面合并则可省略）
- 群主工具集**不**包含 confirm_stage

- [ ] **Step 1:** 在工具注册处根据 `group.decision_maker_participant_id == target_participant_id` 暴露决策工具。

- [ ] **Step 2:** context：`is_decision_maker` 标志 + 短 SOP（例外三类、汇报必做、禁止对外执行）。群主 SOP 改为：阶段确认由决策者/人类管理员负责，群主催证据与执行。

- [ ] **Step 3:** 规则预检（可选轻量）：title/summary 含「预算/打款/合同/上线/客户」等关键词且 LLM 标 routine 时强制升为 `uncertain`（在 service 层 `normalize_category`）。

- [ ] **Step 4:** 测试权限门控与 classify 分支。提交。

```bash
git commit -m "feat(runtime): decision maker tools and SOP context"
```

---

### Task 7: Manager 批准/拒绝 API + 前端设置

**Files:**
- Create: `backend/app/api/group_decisions.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/pages/groups/GroupSettingsModal.tsx`（及必要时 `GroupWorkflowTab.tsx` 显示待批决策）
- Modify: i18n
- Test: API 测试

- [ ] **Step 1:**  
  - `GET /groups/{id}/decisions?status=pending_owner_confirm`  
  - `POST /groups/{id}/decisions/{decision_id}/approve`  
  - `POST /groups/{id}/decisions/{decision_id}/reject` body: `{ reason?: str }`  
  均需人类 manager。approve 成功后：若关联 `stage_id` 且仍 `awaiting_approval`，由服务调用决策者身份 `confirm_stage`；再汇报。

- [ ] **Step 2:** DM 求批文案含决策 ID / 群名 / 建议；指引用户到群设置或工作流页点批准（首版不依赖 NLP 解析私聊回复）。

- [ ] **Step 3:** `GroupSettingsModal`：展示当前决策者；Agent 下拉改绑；汇报对象 multi-select（人类成员，默认提示「全部管理员」）；保存走 patch。

- [ ] **Step 4:** 工作流页可选：manager 可见 pending 决策卡片 + 批准/拒绝按钮。

- [ ] **Step 5:** 前后端冒烟；提交。

```bash
git commit -m "feat(groups): decision approve API and settings UI"
```

---

### Task 8: 验证与收尾

- [ ] `pytest backend/tests/test_group_decision_service.py backend/tests/test_group_decision_seed.py backend/tests/test_group_workflow_decision_wake.py backend/tests/test_group_decision_tools.py -v`（及改过的 group_api / workflow 测试）
- [ ] `cd backend && ruff check app/services/group_decision app/models/group_decision.py app/api/group_decisions.py`
- [ ] `cd frontend && npx tsc --noEmit`（或项目既有 `npm run build`）
- [ ] 对照验收标准自检设计文档 §验收标准 1–5
- [ ] 更新设计文档状态为「已实现」仅在全部验收通过后

---

## Spec coverage checklist

| 规格项 | Task |
|--------|------|
| 专属 Agent + 与群主分离 | 3, 6 |
| 例外三类 + uncertain | 2, 6 |
| 任一 manager 确认 | 2, 7 |
| 阶段确认 + 群主不确认 | 5, 6 |
| 建群自动创建可改绑 | 3, 4 |
| 可配置汇报，默认全部 manager | 2, 4, 7 |
| 每次拍板汇报 | 2 |
| 人类手动确认兜底 | 5（API 不变） |
| 旧群无决策者兼容 | 5（字段可空） |

## 执行方式

Plan 已保存到 `.clawith-local-designs/2026-07-30-group-decision-maker-plan.md`。

**1. Subagent-Driven（推荐）** — 每任务独立子代理，任务间审查  
**2. Inline Execution** — 本会话按任务连续实现并设检查点  

选哪种？
