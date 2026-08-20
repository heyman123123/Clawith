# Intent-Driven Project Orchestrator — Design Spec

**Date**: 2026-08-21
**Author**: Codex (brainstorming skill)
**Track**: Full SDD (cross-module, touches C1/C2/C3/C5 constitution clauses)
**Branch prefix**: `codex/feat/001-intent-driven-orchestrator`
**Constitution reference**: [`docs/constitution.md`](../../../docs/constitution.md)

---

## Overview

Clawith 现有 Agent Templates(20+)、Agents API、Groups/GroupChat、OKR、Triggers、Experience、Focus Items、Skills/MCP 是**离散的能力零件**。本设计把它们**编排为一个意图驱动的 Orchestrator 层**:

> **用户输入自然语言需求 → Orchestrator 多轮 LLM 推理生成 Draft → 用户在 Preview UI 审核 → Atomic Creator 落地(Group + Agents + OKR + Tasks + Chief Runtime) → Chief Runtime(群主 Agent)持续监听 progress events → 审核产物、推进 OKR、派单给执行 Agent、更新看板 → 产物沉淀到 group_file。**

### 关键决策摘要

| 决策 | 选择 |
|---|---|
| Orchestrator 形态 | **B. Persistent Orchestrator**(群主 Agent 在 Runtime 内) |
| Agent 选/创 | **D. 混合**(templates 复用 + LLM 生成新角色 + 落库) |
| 落实语义 | **E. 综合型**(研讨 + 任务 + OKR + Trigger 可选) |
| OKR 推进 | **进度驱动**(产物 verified 推进,非时间) |
| 任务粒度 | **动态**(创建时不穷举,Agent 协作中自发现并新增) |
| 群主角色 | **Chief Agent(Owner/PM)** —— 不干活,只审核 + 调度 + 激活 |
| 触发入口 | **P3. 智能主动识别 + 手动入口**(LLM 判断,可关闭) |
| 审核机制 | **X1. 用户自助审核**(全员可用 + 用户审自己的草稿) |
| Template 共享 | **显式选**(默认 user_private,可升级为 tenant_private/public) |
| 时间字段 | **全部 opt-in** —— Agent 行为默认不绑时间字段 |
| Trigger 系统 | **T1. 保留6 类,默认只用非时间型** |

---

## 1. Requirements (Consolidated)

### 核心需求(9 条)

| # | 需求 | 决策 |
|---|---|---|
| 1 | 综合型 Orchestrator(研讨 + 任务 + OKR + Trigger) | E |
| 2 | OKR 按进度推进(产物 verified 推进,非时间) | 见 §3 schema |
| 3 | 每个阶段完成沉淀产物到群文件 | 复用 `group_file_service` |
| 4 | 混合 Agent 选/创(templates 复用 + LLM 生成) | D + 用户显式选 visibility |
| 5 | 全员可用,用户自助审核(X1) | Draft Preview UI |
| 6 | 每个 Group + Agent 都有任务看板(Kanban) | 新建 `task_board` 模块 |
| 7 | 触发入口:智能主动识别 + 手动入口,LLM 判断,可关闭主动建议 | P3 |
| 8 | 任务是动态的(创建时不穷举,Agent 协作中自发现新增) | `task_cards` 支持运行时创建 |
| 9 | 每个群有一个群主 Agent(Owner/PM),不干活,只审核 + 调度 + 激活 | Chief Runtime 新增 `run_kind="orchestrator"` |

### 全局原则

- **进度驱动优先**: Agent 行为默认由 progress signals 驱动,不用时间字段作为唯一触发
- **时间字段 opt-in**: 所有"什么时候做"的字段(`target_window_*` / `period_*` / `due_date` / `scheduled_at`)只在用户显式提供时落库
- **C1 / C2 / C3 / C5** 严格遵守(详见 §5)
- **多租户隔离**: 所有新表带 `tenant_id NOT NULL`,所有 DAO 强制 tenant scope
- **C3 idempotency**: Atomic Creator 用 `draft_id` 作幂等键;Chief Action 用 `event_id` 作幂等键

### 用户体验原则

- **群主是真 PM**: 不是装饰,真的持续监听 + 推理 + 派单 + 审核
- **审核权 = 用户本人**: 不需要 admin 审批
- **LangGraph Checkpoint 单源真相**: `chief_runs` 表只存元数据
- **Failure gracefully**: 任何子步骤失败可重试 + 补偿,不破坏已创建资源

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          [ User Entry Layer ]                       │
│                                                                     │
│   Web UI:                         Existing Agent Chat:              │
│   • Projects Page                 • Agent 主动识别"项目意图"        │
│   • Chief 1:1 Chat                • 弹出建议:"我帮你建个项目?"      │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ [ Orchestrator Service ]   backend/app/services/orchestrator/      │
│                                                                     │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│   │ Intent       │→ │ Agent        │→ │ Group        │             │
│   │ Analyzer     │  │ Selector     │  │ Planner      │             │
│   │ (LLM)        │  │ (template +  │  │ (OKR +       │             │
│   │              │  │  LLM 生成)   │  │  Tasks)      │             │
│   └──────────────┘  └──────────────┘  └──────────────┘             │
│                                 │                                   │
│                                 ▼                                   │
│   ┌────────────────────┐                                           │
│   │  Draft Renderer    │ ←→  Draft Preview UI(用户审核/编辑)       │
│   └────────────────────┘                                           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ approved
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│   [ Atomic Project Creator ]   单一事务式顺序创建                  │
│                                                                     │
│   1. CREATE Group + Members(含 Chief)                               │
│   2. CREATE OKR + KeyResults                                       │
│   3. CREATE Task Cards                                             │
│   4. CREATE Draft Status = 'consumed'                              │
│   5. ENQUEUE start_chief_run command → agent_run_commands          │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ Command Worker 启动
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│   [ Persistent Orchestrator Runtime ]   run_kind="orchestrator"     │
│                                                                     │
│   ┌──────────────────────────────────────────────────────────┐     │
│   │  Chief Agent LangGraph Checkpoint(专属 thread)              │ │
│   │  • state: 当前 OKR 阶段 / 待审核任务 / 已调度任务         │     │
│   └──────────────────────────────────────────────────────────┘     │
│                                                                     │
│   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐       │
│   │  Event Listener│→ │  Reasoner      │→ │  Action        │       │
│   │ 订阅:          │  │  (LLM 决策)    │  │  Executor      │       │
│   │  • task_done   │  │                │  │  • 审核产物    │       │
│   │  • file_changed│  │                │  │  • 推进 OKR    │       │
│   │  • user_msg    │  │                │  │  • 派单 Agent  │       │
│   │  • progress    │  │                │  │  • 更新看板    │       │
│   └────────────────┘  └────────────────┘  └────────────────┘       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ 派单(RuntimeCommandIntake)
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│              [ Agent Runtime V2 ]   现有,不破坏                    │
│                                                                     │
│   执行 Agent Runtime Run → 完成任务 → 写 group_file                 │
│                                    → 推 task_board_events         │
└────────────────────────────────────────────────────────────────────┘
```

### 端到端数据流

```
[1] 用户 Chat 输入:"我想做一个出海 SaaS 获客方案"
[2] Agent 识别为 project intent → 弹建议
[3] 用户:"好,帮我建"
[4] Orchestrator 多轮 LLM 推理 → 输出 Draft{ group, members[], okr, tasks[], chief_template_visibility }
[5] 用户在 Preview UI 审核、编辑、选 template 共享模式
[6] 批准 → Atomic Creator 顺序执行(幂等键 = draft_id)
[7] Command Worker 接收 start_chief_run → 启动 Chief Runtime
[8] Chief Runtime 开始监听 events
[9] 用户 / 执行 Agent 推进任务 → task_board_event 推送
[10] Chief Reasoner 触发 LLM 决策:推进 OKR / 派新单 / 审核产物
[11] 产物沉淀到 group_file
[12] 用户可与 Chief 1:1 Chat 直接 PM
```

---

## 3. Core Modules

### 模块 1: `orchestrator` Service(全新)

**位置**: `backend/app/services/orchestrator/`

**职责**: 把用户的自然语言需求 → 转换为可创建的 Draft。

**子模块**:

| 文件 | 职责 |
|---|---|
| `intent_analyzer.py` | 多轮 LLM 推理:需求分类 / 范围澄清 |
| `agent_selector.py` | template 匹配 + LLM 生成新角色 |
| `group_planner.py` | OKR + Task Cards 拆分 + Group 结构 |
| `draft_renderer.py` | Draft 标准化输出(供 Preview UI 渲染) |
| `atomic_creator.py` | 顺序原子创建 + 投递 start_chief_run command |
| `draft_schemas.py` | Draft / AgentProposal / OKRProposal / TaskProposal Pydantic |

**关键接口**:

```python
class OrchestratorService:
    async def analyze(user_message: str, tenant_id: UUID, user_id: UUID) -> DraftSummary
    async def propose(tenant_id: UUID, summary: DraftSummary) -> Draft
    async def create(tenant_id: UUID, draft: Draft, draft_id: UUID) -> ProjectCreated
    async def resume(tenant_id: UUID, draft_id: UUID, edits: Draft) -> ProjectCreated
```

**调用关系**:
- 调 `Groups API` / `Agents API` / `OKR API` 实现创建
- 调 `agent_template/registry/` 查 template
- 调 `LLM Gateway` 做意图推理
- **不直接碰 LangGraph Checkpoint / Runtime**(符合 C1)

### 模块 2: `agent_runtime/orchestrator`(扩展现有)

**位置**: `backend/app/services/agent_runtime/orchestrator/`

**职责**: 群主 Agent 的 Runtime —— progress-driven event loop。

**子模块**:

| 文件 | 职责 |
|---|---|
| `orchestrator_run.py` | 新增 `run_kind="orchestrator"` 的 Runtime driver |
| `event_listener.py` | 订阅 task_board_events / group_file_events / user_messages |
| `reasoner.py` | LLM 决策:审核 / 推进 / 派单 / 增删 Task |
| `action_executor.py` | 调 RuntimeCommandIntake + task_board + group_file |
| `progress_signal.py` | 新 trigger 类型 progress_signal 的语义封装 |
| `checkpoint_state.py` | 群主 Checkpoint state schema |

**关键接口**:

```python
class OrchestratorRunLoop:
    async def run(group_id: UUID) -> None      # 阻塞监听直到 group 终止
    async def pause(group_id: UUID) -> None
    async def resume(group_id: UUID) -> None

class EventListener:
    async def subscribe(group_id: UUID) -> AsyncIterator[OrchestratorEvent]

class Reasoner:
    async def decide(event: OrchestratorEvent, group_state: GroupState) -> ActionPlan

class ActionExecutor:
    async def execute(plan: ActionPlan, group_id: UUID) -> None
```

**进度驱动事件源**:

| Event | 来源 | 触发 Action |
|---|---|---|
| `task_done` | TaskBoardService | 群主审核 / 推进 OKR / 派新单 |
| `file_changed` | group_file_service | 群主审核产物 → 更新 OKR stage |
| `user_message` | Chief 1:1 Chat | 直接响应 + 可能派单 |
| `progress_signal` | 任何 Runtime 组件 emit | 自定义进度的钩子 |
| `schedule` | (用户显式 opt-in 的 trigger) | 极少见,仅当用户显式创建 |

**C1 合规**: 群主 Run 通过 `RuntimeCommandIntake` 派单,不直接调执行 Agent 的 LangGraph 节点。

### 模块 3: `task_board`(全新)

**位置**: `backend/app/services/task_board/`

**职责**: Group / Agent 的 Kanban 数据层。

**子模块**:

| 文件 | 职责 |
|---|---|
| `service.py` | TaskBoardService |
| `models.py` | TaskCard / TaskBoardColumn / TaskBoardEvent |
| `event_publisher.py` | 发 task_board_events 到 PG NOTIFY |
| `dao.py` | 严格 batch 查询(防 N+1,符合 C5) |
| `column_defs.py` | 列枚举 |

**关键事件**(发到 PG NOTIFY):

```python
class TaskBoardEvent:
    event_type: str  # card_created / card_moved / card_assigned / card_done / card_commented
```

### 模块 4: `agent_template/registry`(扩展现有)

**位置**: `backend/app/services/agent_template/registry/`

**职责**: Template 的"共享模型"。

**子模块**:

| 文件 | 职责 |
|---|---|
| `visibility.py` | public / tenant_private / user_private |
| `approval.py` | admin 审核工作流 |
| `llm_generator.py` | LLM 生成的 template 落库 |
| `schemas.py` | Pydantic schemas |

**核心枚举**:

```python
class TemplateVisibility:
    PUBLIC = "public"                # 全租户可见
    TENANT_PRIVATE = "tenant_private" # 仅本租户可见
    USER_PRIVATE = "user_private"    # 仅创建者可见(默认 Orchestrator 新生成)

class TemplateApprovalState:
    DRAFT = "draft"                  # LLM 生成,待用户审核
    APPROVED = "approved"            # 用户/管理员已批准
    REJECTED = "rejected"            # 拒绝(进垃圾箱)
```

### 模块依赖关系

```
User → Orchestrator Service → Atomic Creator → ┬→ Groups API
                                                 ├→ Agents API
                                                 ├→ OKR API
                                                 ├→ TaskBoardService
                                                 └→ RuntimeCommandIntake
                                                    (投递 start_chief_run)

User ──chat──→ Chief Agent ──Runtime──→ Orchestrator Run Loop
                                            ├──订阅→ TaskBoard Events
                                            ├──订阅→ Group File Events
                                            └──调→ RuntimeCommandIntake
                                                │
                                                ↓
                                            执行 Agent Runtime
```

---

## 4. Data Model & DB Schema

**统一字段命名**: `template_visibility`(全部统一)

### 表清单

| # | 表 | 动作 | 迁移文件 |
|---|---|---|---|
| 1 | `drafts` | 新建 | `v1_0_0_g001_create_drafts.py` |
| 2 | `task_cards` | 新建 | `v1_0_0_g002_create_task_cards.py` |
| 3 | `task_card_dependencies` | 新建 | `v1_0_0_g003_create_task_card_dependencies.py` |
| 4 | `task_board_events` | 新建 | `v1_0_0_g004_create_task_board_events.py` |
| 5 | `chief_runs` | 新建 | `v1_0_0_g005_create_chief_runs.py` |
| 6 | `agent_templates` | 扩展 | `v1_0_0_g006_extend_agent_templates.py` |
| 7 | `chat_sessions` | 扩展 | `v1_0_0_g007_extend_chat_sessions_chief_run.py` |

### 4.1 `drafts` 表

```sql
CREATE TABLE drafts (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    user_id         UUID NOT NULL,
    user_message    TEXT NOT NULL,
    intent_summary  JSONB,
    draft_payload   JSONB NOT NULL,
    template_visibility VARCHAR(32) NOT NULL DEFAULT 'user_private',
    status          VARCHAR(32) NOT NULL DEFAULT 'pending', -- pending/approved/rejected/expired/error/consumed
    error_detail    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ  -- 运维字段:后台 cleanup job 用,不驱动 agent 行为
);
CREATE INDEX ix_drafts_tenant_id ON drafts(tenant_id);
CREATE INDEX ix_drafts_user_id   ON drafts(user_id);
CREATE INDEX ix_drafts_status    ON drafts(status);
```

### 4.2 `task_cards` 表

```sql
CREATE TABLE task_cards (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    group_id        UUID,                        -- 群看板;NULL = Agent 个人看板
    assignee_agent_id UUID,                      -- 受派 Agent;可空(用户/群主)
    okr_key_result_id UUID,                      -- 派生自 KR;可空(支持动态新增)
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    column          VARCHAR(32) NOT NULL DEFAULT 'backlog', -- backlog/in_progress/blocked/review/done
    position        INTEGER NOT NULL DEFAULT 0, -- 列内排序
    priority        VARCHAR(16),                  -- opt-in: low/normal/high/critical
    tags            JSONB NOT NULL DEFAULT '[]',
    target_window_start TIMESTAMPTZ,             -- ⚙️ opt-in 时间字段
    target_window_end   TIMESTAMPTZ,             -- ⚙️ opt-in 时间字段
    artifact_paths  JSONB NOT NULL DEFAULT '[]', -- 关联群文件路径
    version         INTEGER NOT NULL DEFAULT 0,  -- 乐观锁
    created_by      UUID NOT NULL,
    created_by_type VARCHAR(16) NOT NULL,          -- user / agent / chief
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX ix_task_cards_tenant_id             ON task_cards(tenant_id);
CREATE INDEX ix_task_cards_group_id              ON task_cards(group_id);
CREATE INDEX ix_task_cards_assignee_agent_id     ON task_cards(assignee_agent_id);
CREATE INDEX ix_task_cards_okr_key_result_id     ON task_cards(okr_key_result_id);
CREATE INDEX ix_task_cards_tenant_group_column   ON task_cards(tenant_id, group_id, column);
CREATE INDEX ix_task_cards_assignee_column       ON task_cards(assignee_agent_id, column);
```

注: `depends_on` 字段移除,改用 `task_card_dependencies` 关联表。

### 4.3 `task_card_dependencies` 表

```sql
CREATE TABLE task_card_dependencies (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    task_card_id    UUID NOT NULL,
    depends_on_card_id UUID NOT NULL,
    dependency_type VARCHAR(16) NOT NULL DEFAULT 'blocks', -- blocks / informs / references
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_card_id, depends_on_card_id)
);
CREATE INDEX ix_task_card_dependencies_tenant_id       ON task_card_dependencies(tenant_id);
CREATE INDEX ix_task_card_dependencies_task_card_id    ON task_card_dependencies(task_card_id);
CREATE INDEX ix_task_card_dependencies_depends_on_card ON task_card_dependencies(depends_on_card_id);
```

### 4.4 `task_board_events` 表

```sql
CREATE TABLE task_board_events (
    id UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    group_id      UUID NOT NULL,
    task_card_id  UUID NOT NULL,
    event_type    VARCHAR(64) NOT NULL, -- card_created/card_moved/card_assigned/card_done/card_commented
    payload       JSONB NOT NULL DEFAULT '{}',
    actor_id      UUID NOT NULL,
    actor_type    VARCHAR(16) NOT NULL, -- user / agent / chief
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_task_board_events_tenant_id               ON task_board_events(tenant_id);
CREATE INDEX ix_task_board_events_group_id                ON task_board_events(group_id);
CREATE INDEX ix_task_board_events_task_card_id            ON task_board_events(task_card_id);
CREATE INDEX ix_task_board_events_tenant_group_occurred   ON task_board_events(tenant_id, group_id, occurred_at DESC);
```

### 4.5 `chief_runs` 表

```sql
CREATE TABLE chief_runs (
    id UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    group_id          UUID NOT NULL UNIQUE,        -- 1:1 关系
    chief_agent_id    UUID NOT NULL,
    runtime_thread_id VARCHAR(200) NOT NULL,       -- orchestrator:{group_id}
    status            VARCHAR(32) NOT NULL DEFAULT 'active', -- active/paused/degraded/stopped
    last_event_at     TIMESTAMPTZ,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at        TIMESTAMPTZ,
    failure_count     INTEGER NOT NULL DEFAULT 0,  -- 不死锁机制计数
    last_failure_reason TEXT
);
CREATE INDEX ix_chief_runs_tenant_id ON chief_runs(tenant_id);
CREATE INDEX ix_chief_runs_group_id  ON chief_runs(group_id);
CREATE INDEX ix_chief_runs_status    ON chief_runs(status);
```

### 4.6 `agent_templates` 表扩展

```sql
ALTER TABLE agent_templates
    ADD COLUMN template_visibility VARCHAR(32) NOT NULL DEFAULT 'public',
    ADD COLUMN tenant_id         UUID,                              -- tenant_private 时所属租户
    ADD COLUMN created_by_user_id UUID,
    ADD COLUMN approval_state    VARCHAR(32) NOT NULL DEFAULT 'approved',
    ADD COLUMN rejected_reason   TEXT;

CREATE INDEX ix_agent_templates_visibility ON agent_templates(template_visibility);
CREATE INDEX ix_agent_templates_tenant_visibility ON agent_templates(tenant_id, template_visibility);
```

### 4.7 `chat_sessions` 表扩展

```sql
ALTER TABLE chat_sessions
    ADD COLUMN chief_run_id UUID;  -- 非空时 = 这是 Chief 1:1 Chat
CREATE INDEX ix_chat_sessions_chief_run_id ON chat_sessions(chief_run_id);
```

### 4.8 Chief Run 启动路径(明确)

```
Atomic Creator
  ├─ CREATE Group + Members
  ├─ CREATE OKR + KeyResults
  ├─ CREATE Task Cards
  ├─ UPDATE drafts.status = 'consumed'
  └─ INSERT INTO agent_run_commands (command_type='start_chief_run', ...)
        ↓
   Command Worker 接收
        ↓
   启动 Chief Runtime(类似 trigger_daemon 启动)
        ↓
   Chief 开始监听 events

注: Atomic Creator 不直接启动 Runtime,避免 C1 违规
```

---

## 5. Constitution Check(C1-C6 逐条论证)

### C1 — Runtime Boundary Isolation ⚠️ 重点

| 规则 | 设计 | 证据 |
|---|---|---|
| API 层只通过 RuntimeCommandIntake 派单 | Orchestrator Service 不直接调 LangGraph 节点 | `orchestrator/atomic_creator.py` |
| Chief Runtime 通过 RuntimeCommandIntake 派单 | Chief ActionExecutor 走 RuntimeCommandIntake | `agent_runtime/orchestrator/action_executor.py` |
| Checkpoint 由现有 checkpointer 管 | chief_runs 只存元数据 | chief_runs 表 vs `checkpointer.py` |
| 产品层不能成为第二执行状态机 | TaskBoard / OKR 是产物,不是状态机 | TaskBoardService 只 emit events,不重跑 Agent |
| Chief Run 启动走 Command Worker | Atomic Creator 投递 start_chief_run command | §4.8 |

### C2 — Strict Multi-Tenant Scope ✅

- 所有 6 张新表有 `tenant_id NOT NULL`
- 所有 DAO 函数强制 `where(Model.tenant_id == tenant_id)`
- Cache key 前缀 `tenant:{tenant_id}:drafts:{id}` / `task_board:{group_id}`
- EventListener / Reasoner 接受 `tenant_id` 参数,ContextVar 注入

### C3 — Idempotent Side Effects & Reconciliation ✅

| Action | 幂等性策略 |
|---|---|
| Atomic Creator 创建 Group/Agents/OKR/Tasks | UUID 客户端生成,重试用同一 ID;**幂等键 = `draft_id`** |
| Chief 派单 RuntimeCommandIntake | RuntimeCommandIntake 本身幂等(基于 `command_id`) |
| Chief 更新 OKR 阶段 / Task column | 乐观锁(`version` 字段);**幂等键 = `event_id`** |
| Chief 审核产物 | 幂等键 = `(group_id, task_card_id, artifact_path)` 已 verified 列表 |

### C4 — Client & Gateway Wrapper Enforcement ✅

- 前端: 新增 `frontend/src/services/orchestrator.ts`(走 `src/api/request.ts`),禁止 `import axios`
- 后端: Orchestrator 调 LLM 走统一 `app/services/llm/` 网关(不直接调 OpenAI/Anthropic API)

### C5 — No FK + N+1 Prevention ✅

- **零物理 FK**(只在 chat_sessions 扩展时发现历史 FK,本次不修)
- 关键索引:
  - `task_cards (tenant_id, group_id, column)` 看板主查询
  - `task_board_events (tenant_id, group_id, occurred_at DESC)` 事件流
  - `chief_runs (group_id UNIQUE)` Chief 与 Group 1:1
- DAO 层强制 `selectinload` / `in_()`(防止 N+1)

### C6 — Code Modularity ✅

- `orchestrator/` 包按职责拆分 7 个子模块(每个文件 ~100-300 行)
- `agent_runtime/orchestrator/` 同样 7 个子模块
- `task_board/` 4 个文件,每个 ~200 行
- 总体符合 C6 推荐阈值

---

## 6. Error Handling & Edge Cases

### 6.1 故障分类表

| 层级 | 故障场景 | 严重度 | 处理策略 |
|---|---|---|---|
| L1 入口 | LLM 误判"非项目意图" | 低 | 静默忽略 |
| L1 入口 | 用户拒绝 LLM 建议 | 低 | 记日志,降级识别阈值 |
| L2 Orchestrator | IntentAnalyzer LLM 超时 | 中 | 重试 3 次(指数退避);fallback gpt-4o-mini |
| L2 Orchestrator | IntentAnalyzer JSON 无法解析 | 中 | 重试 + 不同 prompt;连续 3 次失败报错 |
| L2 Orchestrator | Draft 创建后用户 24h 未批 | 低 | 后台 job 标记 status=expired,清理 |
| L3 Atomic Creator | 顺序创建第 N 步失败 | 高 | 回滚已创建资源;Draft=error;保留错误信息 |
| L3 Atomic Creator | 部分资源已创建 + 重试 | 高 | 幂等键=draft_id,重试用同一 UUID,ON CONFLICT DO NOTHING |
| L4 Chief Runtime | LangGraph Checkpoint 写失败 | 高 | 重试 3 次;失败触发 reconciliation + admin 告警 |
| L4 Chief Runtime | EventListener PG NOTIFY 断开 | 中 | 重连;每 30s 补偿查询:task_board_events WHERE occurred_at > last_event_at |
| L4 Chief Runtime | Reasoner LLM 调用失败 | 中 | 任务挂起 + TaskCard.column=blocked;Chief 不死锁(失败 5 次 → 告警) |
| L4 Chief Runtime | Chief 自己卡住(连续无响应 5 分钟) | 高 | Health check 触发;自动 pause + 通知用户 + admin |
| L4 Chief Runtime | ActionExecutor RuntimeCommandIntake 失败 | 中 | RuntimeCommandIntake 重试;失败 3 次 → TaskCard=blocked + Reasoner 重规划 |
| L5 Task Board | column 非法状态转移(done→in_progress) | 中 | 状态机拒绝 + 报错 |
| L5 Task Board | task_card_dependencies 形成循环 | 中 | DAG 检测;插入前校验,拒绝 |
| L5 Task Board | 同 TaskCard 同时被多 Actor 操作 | 中 | 乐观锁(version) + 重试 |
| L6 Template | LLM 生成新 template 失败 | 低 | 回退到 template 复用 |
| L6 Template | Admin 审核工作流异常 | 中 | 邮件通知 + Draft 状态保留 |
| L7 多租户 | 跨租户访问 draft / task_card | 高 | C2 强制拒绝(DAO 层 tenant_id scope) |
| L7 多租户 | User 删除 group | 中 | chief_runs 软删除(stopped_at) + Chief Runtime 收到终止信号自杀 |
| L8 容量 | 单 group task_card > 1000 | 低 | 软警告 + 建议拆分 |
| L8 容量 | Chief EventListener 积压 | 中 | 限流 + 批量处理 |

### 6.2 关键设计:Chief 的不死锁机制

```
Reasoner 连续失败 5 次
  ↓
Chief 自动健康检查(Health Check Daemon)
  ↓
checkpoint state = "degraded"
  ↓
├── 暂停 Reasoner LLM 调用(冷却 5 分钟)
├── 给用户 Chief 1:1 Chat 发告警:"我卡住了,等一下或帮我看下"
└── 给 admin 发通知
  ↓
冷却结束 → 自动恢复尝试
  ↓
若仍失败 → 标记 Chief Run = "stopped",等用户/admin 介入
```

### 6.3 Chief 决策与执行偏离的 Reconciliation

C3 原则: "A committed checkpoint remains authoritative even if product synchronization temporarily fails."

- Chief Action 写入 Checkpoint 是权威
- TaskCard.column / OKR.status 是投影
- 异步 reconciliation job 每 5 分钟扫一次:
  - Checkpoint state vs 实际 DB 状态
  - 不一致 → 用 Checkpoint state 覆盖(Checkpoint 是 single source of truth)

---

## 7. Test Strategy

按 Clawith 现有测试组织(`backend/tests/`)。

### 7.1 测试金字塔

```
┌─────────────────────────────────┐
│      E2E (Playwright + Manual)  │  10%
├─────────────────────────────────┤
│   Integration (pytest-asyncio)  │  30%
├─────────────────────────────────┤
│      Unit (pytest)              │  60%
└─────────────────────────────────┘
```

### 7.2 测试矩阵

| 测试类别 | 工具 | 覆盖目标 |
|---|---|---|
| Unit: Orchestrator | pytest + mock LLM | IntentAnalyzer / AgentSelector / GroupPlanner / DraftRenderer 每个函数 |
| Unit: TaskBoard | pytest + in-memory SQLite | TaskBoardService 所有方法,状态机,乐观锁,循环检测 |
| Unit: Template Registry | pytest + mock LLM | visibility / approval_state / LLM generator |
| Unit: ActionExecutor | pytest + mock RuntimeCommandIntake | 派单逻辑 / 幂等键 / 失败重试 |
| Integration: Draft → Create | pytest-asyncio + real DB | 完整 Draft 流程,schema 一致性 |
| Integration: Event flow | pytest-asyncio + real PG(NOTIFY) | task_done → Chief Reasoner → ActionExecutor 闭环 |
| Integration: Multi-tenant | pytest-asyncio | 跨 tenant 访问应失败(C2) |
| Integration: Idempotency | pytest-asyncio | Atomic Creator 重试产生相同结果(C3) |
| E2E: UI flow | Playwright | 用户在 Web UI 走完需求→草稿→批准→看板→Chief 审核 |
| Constitution | scripts/arch-guard.sh + 自定义 | C1-C6 每条 |
| LLM reasoning | pytest + mock response | IntentAnalyzer / Reasoner 解析 + fallback |

### 7.3 关键测试用例(必须)

| 用例 ID | 场景 | 期望 |
|---|---|---|
| T-001 | 用户在 Chat 输入"帮我做 X" → Agent 主动建议 | 弹出建议卡片,不打断对话 |
| T-002 | Orchestrator 多轮推理 → 输出完整 Draft | Draft 包含 group/members/okr/tasks |
| T-003 | 用户在 Draft Preview 编辑 task title | Draft 更新,批准后用最新 title |
| T-004 | Atomic Creator 顺序创建 | 6 步全部成功,DB 一致 |
| T-005 | Atomic Creator 第 3 步失败 | 前 2 步回滚,Draft=error |
| T-006 | Atomic Creator 重试(同 draft_id) | 幂等,无重复资源 |
| T-007 | Chief Runtime 启动后订阅 events | EventListener 连接到 PG NOTIFY |
| T-008 | task_done 事件 → Chief Reasoner → 派单 | 完成派单链路,TaskCard.column 更新 |
| T-009 | Chief Reasoner LLM 失败 5 次 | Chief 标记 degraded + 用户告警 |
| T-010 | 任务依赖形成环 | DAG 检测拒绝 |
| T-011 | TaskCard column 非法转移 | 状态机拒绝 |
| T-012 | 跨租户访问 task_card | 403 Forbidden |
| T-013 | LLM 生成新 template 失败 | 回退到 template 复用 |
| T-014 | Chief 1:1 Chat 创建 | chat_sessions.chief_run_id 正确填充 |
| T-015 | OKR 阶段 verified 条件达成 | OKR 阶段自动推进 |
| T-016 | 用户选 template_visibility=user_private | 新 template 只本人可见 |
| T-017 | 用户选 template_visibility=tenant_private | 进入 admin 审核队列 |
| T-018 | Chief Action 失败 → reconcile | Checkpoint 覆盖投影 |

### 7.4 测试 Fixture 设计

```python
# tests/conftest.py 扩展
@pytest.fixture
async def tenant_factory():
    """创建测试租户(独立 tenant_id)"""

@pytest.fixture
async def draft_factory():
    """创建测试 Draft 草稿"""

@pytest.fixture
async def chief_run_factory():
    """创建测试 Chief Runtime(独立 thread_id)"""

@pytest.fixture
async def mock_llm():
    """Mock LLM response,避免真实调用"""

@pytest.fixture
async def notify_listener():
    """启动 PG LISTENER,捕获 task_board_events"""
```

### 7.5 覆盖率目标

| 模块 | 目标 |
|---|---|
| `orchestrator/` | ≥ 85% |
| `agent_runtime/orchestrator/` | ≥ 80% |
| `task_board/` | ≥ 90% |
| `agent_template/registry/` | ≥ 80% |
| Constitution Check 脚本 | 100% |

---

## 8. Self-Review & Resolved Issues

### Issue #1: `template_visibility` vs `visibility` 字段命名不统一 ✅ 已解决

- 决策: **统一为 `template_visibility`**
- 涉及:`drafts` 表、`agent_templates` 表、所有 API schema
- §4 已统一

### Issue #2: `drafts.expires_at` 与"agent 不绑时间"原则的张力 ✅ 已解决

- 决策: **保留 `expires_at`,标为后台 cleanup 用,不驱动 agent 行为**
- 文档说明: §4.1 表注释明确"`expires_at` 是运维字段:后台 cleanup job 用,不驱动 agent 行为"
- 与 `created_at` / `updated_at` 同等地位(都是审计/运维字段,不是行为驱动)

### Issue #3: Chief Run 启动路径未完全澄清 ✅ 已解决

- 决策: **Atomic Creator 投递 `start_chief_run` command → Command Worker 启动 Chief Runtime**
- 文档: §4.8 详细说明
- 与现有 trigger_daemon 启动模式一致
- 避免 C1 违规

### 二次自检: 内部一致性

| 检查项 | 结果 |
|---|---|
| Section 1 架构 vs Section 2 模块拆分 | ✅ 一致 |
| Section 3 schema vs Section 2 接口契约 | ✅ 一致(字段命名 / 表名都对齐) |
| Section 4 Constitution Check vs Section 1 决策表 | ✅ 一致 |
| Section 6 错误处理 vs Section 2 事件源 | ✅ 一致(task_done / file_changed / user_message 全覆盖) |
| Section 7 测试用例 vs Section 2 接口 | ✅ 覆盖所有模块 |
| 9 条核心需求 vs 设计 | ✅ 全覆盖 |

---

## 9. Open Questions & Next Steps

### Open Questions

| 问题 | 建议处理时机 |
|---|---|
| Chief 是否需要"记忆压缩"(Checkpoint state 过大) | 实现期间评估,可能需要 compaction daemon |
| 用户手动 trigger `start_chief_run` 之外的 Chief 行为(如"立即审核") | v2 增强 |
| 多 Chief 协作(1 group 多个 Chief) | v2 增强 |
| 用户加入 Group Chat 与所有 Agent 互动(方案 C) | v2 长期愿景 |

### Next Steps

按 AGENTS.md §4 SDD 工作流:

```
1. ✅ Spec Discovery (本文档 §1)
2. ✅ spec.md / design.md (本文档 §1-7,合并版)
3. → User reviews 本文档 ★
4. tasks.md 拆分(基于本文档 §3 / §4)
5. Branch feat/001-intent-driven-orchestrator
6. Wave-by-Wave 实现:
    Wave 1: DB 迁移 + draft / task_cards 模型 + DAO 层
    Wave 2: Orchestrator Service(LLM 推理 + Draft)
    Wave 3: TaskBoard Service + 事件流
    Wave 4: Chief Runtime(Orchestrator Run Loop + Reasoner + Action)
    Wave 5: Template Registry 扩展 + LLM 生成
    Wave 6: 前端 Projects 页 + Draft Preview UI + Chief 1:1 Chat
    Wave 7: E2E 测试 + 文档 + arch-guard
7. scripts/arch-guard.sh + test suite
8. Code Review & Merge
```

### Scope Check

| 维度 | 评估 |
|---|---|
| 代码量预估 | Backend ~3500 行(含7 模块 + 测试);Frontend ~1000 行 |
| DB 迁移 | 7 个迁移文件(g001-g007),5 个新表 + 2 个表扩展 |
| API 端点新增 | ~12 个(orchestrator + task_board + chief + template registry) |
| 时间预估 | 4-6 周(Persistent Orchestrator 方案 B) |
| 风险点 | Chief Runtime 是新形态,需仔细测试不死锁机制;LLM 推理成本需监控 |

### Constitution 6 条全部 check 过 ✅

C1 / C2 / C3 / C4 / C5 / C6 全部满足。可进入 implementation。
