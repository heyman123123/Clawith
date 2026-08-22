# 04 - End-to-End Flows & Sequence Diagrams

> **状态**: 🟡 DRAFT
> **范围**: Intent-Driven Project Orchestrator 的端到端流程

---

## 1. 总体旅程图（Activity Diagram）

```mermaid
flowchart TD
    Start([用户进入 Projects 页面]) --> Input[输入自然语言目标]
    Input --> Propose[点击 生成]
    Propose --> Orch{LLM 推理成功?}
    Orch -->|否| Error[显示错误<br>可重试]
    Error --> Input
    Orch -->|是| Draft[跳转到 DraftPreview]
    Draft --> Edit[编辑 Group 名 / 描述 / Agent / Task]
    Edit --> Vis[选择 Template Visibility]
    Vis --> Approve[点击 Approve]
    Approve --> Atomic[Atomic Creator 落地]
    Atomic --> Chief[Chief Runtime 启动]
    Chief --> Group[跳转到 Project Detail / Group]
    Group --> Work[Task Board + ChiefChat 持续工作]
    Work -->|产物 verified| Advance[推进 OKR / Task]
    Work -->|新发现| NewCard[执行 Agent 新增 Task Card]
    NewCard --> Work
    Advance --> Work
    Work -->|用户停止| Stop[Chief stopped]
    Work -->|Group 归档| Stop
    Stop --> End([结束])
```

---

## 2. 详细序列图：Draft 提案 → 落地

### 2.1 提案生成

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant P as Projects.tsx
    participant OS as orchestrator.ts (FE service)
    participant API as /api/orchestrator/drafts
    participant OSE as OrchestratorService
    participant IA as IntentAnalyzer
    participant AS as AgentSelector
    participant GP as GroupPlanner
    participant LLM as LLM Gateway
    participant DB as PostgreSQL

    U->>P: 输入「帮我做 ...」并点击生成
    P->>OS: orchestratorApi.proposeDraft(msg)
    OS->>API: POST /orchestrator/drafts { user_message }
    API->>OSE: OrchestratorService.propose(msg, user, tenant)
    OSE->>IA: analyze(msg)
    IA->>LLM: prompt(IntentAnalyze)
    LLM-->>IA: {intent_category, scope_summary, key_constraints}
    IA-->>OSE: IntentSummary
    OSE->>AS: select(intent_summary, tenant)
    AS->>DB: SELECT templates WHERE tenant_id=? AND visibility IN (...)
    AS->>LLM: prompt(AgentPropose, gap=剩余角色)
    LLM-->>AS: [{role, name, system_prompt, is_new_template}]
    AS-->>OSE: AgentProposals (模板 + LLM 新生成)
    OSE->>GP: plan(agents, intent)
    GP->>LLM: prompt(PlanOKR + PlanTasks)
    LLM-->>GP: {objective, key_results[], tasks[]}
    GP-->>OSE: GroupPlan
    OSE->>DB: INSERT INTO drafts (... JSON ...)
    DB-->>OSE: draft.id
    OSE-->>API: Draft (含 id, status=pending)
    API-->>OS: 201 Created
    OS-->>P: Draft
    P->>P: navigate(`/projects/draft/${draft.id}`)
```

### 2.2 审核 + 落地

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant DP as DraftPreview.tsx
    participant OS as orchestrator.ts (FE)
    participant API as /api/orchestrator/drafts
    participant AC as atomic_creator (legacy / new)
    participant RCI as RuntimeCommandIntake
    participant ARC as agent_run_commands
    participant DB as PostgreSQL
    participant CH as Chief Runtime
    participant LG as LangGraph

    U->>DP: 编辑 visibility 选择 + 点 Approve
    DP->>OS: orchestratorApi.approveDraft(draftId)
    OS->>API: POST /orchestrator/drafts/{id}/approve
    API->>DB: UPDATE drafts SET status='approved', approved_at=NOW
    DB-->>API: OK
    API-->>OS: Draft (approved)

    U->>DP: 点 Approve & Create
    DP->>OS: orchestratorApi.createDraft(draftId)
    OS->>API: POST /orchestrator/drafts/{id}/create
    API->>DB: BEGIN; SELECT * FROM drafts WHERE id=? FOR UPDATE
    API->>AC: materialize(draft, user, tenant)
    AC->>DB: INSERT Group + GroupMembers (含 Chief)
    AC->>DB: INSERT Agent (从模板克隆或新生成)
    AC->>DB: INSERT OKR (objective + key_results)
    AC->>DB: INSERT TaskCards (批量, status='todo')
    AC->>DB: INSERT TaskCardDependencies
    AC->>DB: INSERT chief_runs (status='created')
    AC->>RCI: submit_start_chief_run(group_id, chief_agent_id)
    RCI->>ARC: INSERT AgentRun + AgentRunCommand(start)
    AC->>DB: UPDATE drafts SET status='consumed'
    AC->>DB: COMMIT
    AC-->>API: {group_id, session_id, chief_run_id, okr_objective_id, task_card_ids}
    API-->>OS: ProjectCreated
    OS-->>DP: ProjectCreated
    DP->>DP: navigate(`/groups/${group_id}?session=${session_id}&name=...`)

    Note over CH,LG: 异步 - 后台
    ARC-->>CH: Command Worker picks up
    CH->>LG: invoke graph turn
    LG-->>CH: checkpoint (state=ready)
    CH->>DB: UPDATE chief_runs SET status='running'
```

---

## 3. 详细序列图：Chief Runtime 持续调度

```mermaid
sequenceDiagram
    autonumber
    participant EX as Executor Agent
    participant TBS as TaskBoardService
    participant DB as task_cards
    participant EBP as event_publisher
    participant PG as PG LISTEN/NOTIFY
    participant CH as Chief Runtime
    participant REA as Reasoner
    participant LLM as LLM Gateway
    participant AEX as ActionExecutor
    participant LG as LangGraph Checkpoint

    Note over CH,LG: Long-lived loop

    PG->>CH: NOTIFY task_board_events (event_id)
    CH->>EBP: fetch_pending_events(tenant_id, group_id)
    EBP->>DB: SELECT * FROM task_board_events WHERE consumed_at IS NULL
    EBP-->>CH: [events]
    CH->>REA: reason(events, chief_state)
    REA->>LLM: prompt(PlanActions, events)
    LLM-->>REA: ActionPlan [{type, target, payload}]
    REA-->>CH: ActionPlan
    CH->>AEX: execute(plan)
    AEX->>TBS: review_card / advance_okr / dispatch_task
    TBS->>DB: UPDATE task_cards SET status, version+=1
    TBS->>EBP: publish(event)
    EBP->>PG: NOTIFY
    AEX->>DB: UPDATE task_board_events SET consumed_at=NOW (idempotency)
    AEX-->>CH: done
    CH->>LG: invoke graph turn (post-action reflection)
    LG-->>CH: new checkpoint
    CH->>CH: sleep / wait NOTIFY
```

---

## 4. 详细序列图：TaskCard 生命周期

```mermaid
sequenceDiagram
    autonumber
    participant CR as Creator<br>(AtomicCreator / Executor / Chief)
    participant TBS as TaskBoardService
    participant DB as task_cards
    participant AS as Assignee Agent
    participant RV as Reviewer<br>(Chief / Owner)
    participant EBP as event_publisher

    CR->>TBS: create_card(title, description, deps)
    TBS->>DB: INSERT status='todo'
    TBS->>EBP: publish('task.created')

    AS->>TBS: claim_card(card_id)
    TBS->>DB: UPDATE status='in_progress', assignee=AS
    TBS->>EBP: publish('task.claimed')

    AS->>TBS: submit_card(card_id, artifact_paths)
    TBS->>DB: UPDATE status='review'
    TBS->>EBP: publish('task.submitted')

    alt Reviewer 批准
        RV->>TBS: approve_card(card_id, verified_artifacts)
        TBS->>DB: UPDATE status='done', verified_artifacts=[...]
        TBS->>EBP: publish('task.done')
    else Reviewer 拒绝
        RV->>TBS: reject_card(card_id, review_feedback)
        TBS->>DB: UPDATE status='in_progress', review_feedback
        TBS->>EBP: publish('task.rejected')
    end
```

---

## 5. 详细序列图：模板可见性审核

```mermaid
sequenceDiagram
    autonumber
    participant LLM as LLM Generator
    participant TR as TemplateRegistryService
    participant DB as agent_templates
    participant ADM as Admin (org / platform)
    participant UI as EnterpriseSettings UI

    LLM->>TR: propose_template(role, system_prompt, suggested_visibility)
    alt visibility = user_private
        TR->>DB: INSERT visibility='user_private', approval_state='approved'
    else visibility = tenant_private
        TR->>DB: INSERT visibility='tenant_private', approval_state='pending'
        ADM->>UI: 打开待审列表
        UI->>TR: list_pending_templates()
        ADM->>UI: 点击 Approve
        UI->>TR: approve_template(id)
        TR->>DB: UPDATE approval_state='approved', approved_by=admin
    else visibility = public
        TR->>DB: INSERT visibility='public', approval_state='pending'
        Note over ADM,UI: 需 platform_admin 审批
    end
```

---

## 6. 错误流：Draft 落地失败

```mermaid
flowchart TD
    Start[AtomicCreator 开始] --> T1[创建 Group]
    T1 -->|失败| R1[回滚 + 设置 Draft.status='error']
    T1 -->|成功| T2[创建 Members]
    T2 -->|失败| R2[DELETE Group + Draft.status='error']
    T2 -->|成功| T3[创建 OKR]
    T3 -->|失败| R3[DELETE Group/Members + Draft.status='error']
    T3 -->|成功| T4[创建 TaskCards]
    T4 -->|失败| R4[DELETE Group/Members/OKR + Draft.status='error']
    T4 -->|成功| T5[投递 start_chief_run]
    T5 -->|失败| R5[保留 Group（孤儿） + Draft.status='error']
    T5 -->|成功| T6[UPDATE Draft.status='consumed']
    T6 --> End([返回 ProjectCreated])

    R1 & R2 & R3 & R4 --> Notify[通知用户 + 写 audit log]
    R5 --> Manual[标记需人工处理]
```

---

## 7. 错误流：Chief Runtime 持续失败

```mermaid
flowchart TD
    Loop[每次 Reasoner 调用] -->|成功| Reset[reset failure_count]
    Loop -->|失败| Inc[+1 failure_count]
    Inc --> Check{count >= 5?}
    Check -->|否| Retry[cooldown 300s 后重试]
    Check -->|是| Degraded[status='degraded']
    Degraded --> Recovery[继续监听; 等待人工干预]
    Retry --> Loop
    Reset --> Loop
```

---

## 8. 错误流：Draft 过期 / 重复提交

```mermaid
flowchart TD
    Submit[POST /drafts/{id}/create] --> S1{status 是什么?}
    S1 -->|pending| E1[403 需先 approve]
    S1 -->|approved| S2[进入落地流程]
    S1 -->|consumed| E2[409 已落地，幂等拒绝]
    S1 -->|rejected/expired| E3[400 状态不允许]
    S1 -->|error| E4[409 错误状态，需先重试]
```

---

## 9. 已知 Bug / 待修复

- 🟡 **BUG-04-A**: 上述 `OrchestratorService.propose` 流程中的具体 LLM prompt 模板未在文档中给出，隐藏在 `intent_analyzer.py` / `agent_selector.py` / `group_planner.py` 中。需要后续 review 时把这些 prompt 也纳入 spec。
- 🟡 **BUG-04-B**: 序列图 5.2 中 `atomic_creator (legacy / new)` — README 提到 `atomic_creator.py` 被标记为 LEGACY，但 git 修改显示 `orchestrator.py` 重写了相关逻辑，**实际替代实现的路径**需要在文档中明确。
- 🟡 **BUG-04-C**: 序列图 5.3 中 `Reasoner` 的 prompt 与 `ActionExecutor` 的实际行为（review vs dispatch vs advance_okr）**未在代码中完全验证**，文档基于已有 spec 推测。
- 🟡 **BUG-04-D**: 错误流 6 中「保留 Group（孤儿）」是否被接受（避免回滚成本）需要在 atomic_creator 实现中确认。
- 🟡 **BUG-04-E**: 序列图 2.1 中「AgentSelector 从模板 + LLM 生成混合选择」的优先级规则（先填模板再补 LLM？按 role 匹配？按 tenant 默认？**未完全验证**）。
- 🟡 **BUG-04-F**: 序列图 2.2 中 `submit_start_chief_run` 后立即 UPDATE `chief_runs.status='running'` — 实际可能是异步的（worker 拉起后才更新），文档基于同步视角。