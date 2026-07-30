# 群决策者（Decision Maker）— 设计

日期：2026-07-30  
状态：已实现（待联调 / alembic upgrade）  
分支：`feat/add-flow`（或后续功能分支）

## 背景与问题

群内现有角色是人类 `manager` / `member` + Agent **群主（leader）**。阶段确认（`confirm_stage`）仅人类 manager 可点；群主负责编排与催办，但不能替项目拍板。业务需要一个**专属决策者 Agent**：在常规事项上代用户做项目级决策并推进；仅在例外（人沟通 / 对外部署 / 财务，或拿不准）时私聊人类群管理求确认；拍板后还要把决策**简要私聊汇报**给可配置的汇报对象。

## 目标

1. 每群有可绑定的 **决策者 Agent**（与群主分离），创建群/团队时自动创建并可改绑。
2. 阶段待确认、计划/优先级/阻塞类项目决策由决策者分流：常规直接拍板，例外私聊人类 manager（任一确认即可）。
3. 群主继续执行编排，**不**承担 `confirm_stage`。
4. 决策者每一次生效拍板后，向**可配置汇报对象**发送简短私聊汇报（默认全部人类 manager）。
5. 人类 manager 手动确认仍保留为兜底/覆盖。

## 非目标（首版）

- 董事会 / 多级 escalation
- 决策者自动执行对外部署、打款、代替人类对外沟通
- 用决策者取代群主编排角色
- 把汇报做成日报汇总（首版为**每次决策即时**私聊）

## 需求决策记录

| 项 | 选择 |
|----|------|
| 决策者是谁 | A：专属 Agent，与群主分离 |
| 例外找谁确认 | B：全部人类 manager，任一确认即可 |
| 例外规则 | A：固定三类（人沟通 / 对外部署 / 财务）+ 拿不准就升级 |
| 决策范围 | B：阶段确认 + 计划/优先级/阻塞；群主执行 |
| 入驻方式 | C：建群/建团队自动创建，可改绑 |
| 拍板后汇报 | C：可配置汇报对象，默认全部人类 manager；每次决策即时简短私聊 |

## 方案（推荐）

**`Group.decision_maker_participant_id` + seed Decision Maker Agent + 工作流闸门唤醒 + 分类工具/指令 + 私聊确认与汇报桥接。**

不采用：让群主兼任决策者；或仅 UI 多一个「决策」按钮而无 Agent。

### 角色分工

| 角色 | 职责 |
|------|------|
| 决策者 Agent | 分类、常规拍板、例外求批、确认阶段/记录决策、事后汇报 |
| 群主 Agent | 编排、催证据、公开说明、按已拍板结论执行 |
| 人类 manager | 例外确认；随时手动覆盖 `confirm_stage`；默认可收汇报 |
| 人类 member | 执行与交证据，不参与拍板 |

## 数据模型

### `groups` 新增

| 字段 | 类型 | 说明 |
|------|------|------|
| `decision_maker_participant_id` | UUID FK → participants，可空 | 本群决策者；空则回退仅人类确认（兼容旧群） |
| `decision_report_participant_ids` | JSON 数组，可空 | 汇报对象 participant UUID 列表；**`null` = 默认全部人类 manager**；`[]` = 不发汇报 |

改绑决策者：校验目标为同租户 Agent participant，且宜为本群成员（若不在群则自动邀请为 member，角色不升为 manager）。

### `GroupDecisionRequest`（新表，建议）

| 字段 | 说明 |
|------|------|
| `id`, `tenant_id`, `group_id`, `workflow_id?`, `stage_id?` | 关联 |
| `decision_maker_participant_id` | 发起方 |
| `category` | `routine` \| `human_comms` \| `external_deploy` \| `finance` \| `uncertain` |
| `title`, `summary`, `recommendation`, `options_json` | 简要决策内容 |
| `status` | `pending_owner_confirm` \| `approved` \| `rejected` \| `auto_applied` \| `cancelled` |
| `approver_participant_id?`, `decided_at?` | 任一 manager 确认后写入 |
| `report_sent_at?` | 汇报发送时间 |
| `created_at`, `updated_at` | |

用于例外挂起、审计与去重；`routine` 也可写一条 `auto_applied` 便于汇报与追溯。

## 行为设计

### 入驻

- 创建执行群 / HR 团队向导建群时：seed「决策者」Agent（soul/指令强调代用户拍板、例外升级、每次汇报），加入群，写入 `decision_maker_participant_id`。
- 群设置可改绑；可配置 `decision_report_participant_ids`（默认 null = 全部 manager）。

### 决策触发时机

阶段进入 `awaiting_approval`（证据齐套 / OKR 等强制闸）时：

1. 仍可发群主 wake（催证据、公开说明）
2. **同时**唤醒决策者（`decision_action` 或等价事件 + @决策者）
3. 决策者读取阶段目标、验收标准、证据摘要后分类

计划/优先级/阻塞等非阶段闸门：由群主或成员 @决策者，或工作流事件带 `needs_decision` 时唤醒（首版以阶段闸门为主路径，其他走 @ 触发）。

### 分类与动作

| 类别 | 行为 |
|------|------|
| `routine` | 直接确认阶段 / 记录项目决策 → `auto_applied` → **发汇报** |
| `human_comms` / `external_deploy` / `finance` | 进入例外私聊求批 |
| `uncertain` | 同例外 |

### 例外私聊流

1. 决策者对人类 manager 开/用 **direct** 会话说明：待决事项、选项、风险、建议（求批，非汇报）。
2. `GroupDecisionRequest.status = pending_owner_confirm`。
3. **任一** manager 确认（私聊明确回复或 UI「批准」）→ 决策者执行确认并推进 → `approved` → **发汇报**。
4. 拒绝/改方案 → `rejected`；决策者回群公开说明；群主按新指令执行；阶段可不完成；**仍发汇报**（结论为「未通过 / 已改方案」）。

### 拍板后汇报（必做）

- **时机**：决策状态变为 `auto_applied` / `approved` / `rejected`（含改方案定稿）后立即发送。
- **通道**：决策者 → 各汇报对象的 **direct** 私聊（与求批会话可复用同一线程，但文案标明「决策汇报」）。
- **对象**：解析 `decision_report_participant_ids`；`null` → 当前群全部 `role=manager` 的人类 participant；`[]` → 跳过；显式列表 → 仅这些人（需仍为有效群成员，失效则跳过并记日志）。
- **内容极简**：事项标题、结论、依据一两句、类别（常规/例外）、是否经过人类确认、相关阶段名。
- **与求批区分**：例外求批是行动请求；汇报是事后告知，不要求回复。

### 与现有闸门 / OKR

- 人类 manager 手动「确认并进入下一阶段」保留。
- 群主 **不能** `confirm_stage`。
- 决策者在 `routine` 或例外已获批后可调用确认工具/API。
- OKR「阶段到达」仍以阶段**真正确认**后为准（决策者或人类均可触发到达类事件）。

### 决策者能力边界

允许：读工作流上下文、分类、确认阶段、记录决策理由、发起 manager 求批、发汇报。  
禁止：对外部署动作、财务操作、代替人类对外沟通（只能建议 + 等确认）。

## 主要落点（实现时）

1. Migration：`decision_maker_participant_id`、`decision_report_participant_ids`、`group_decision_requests`
2. Seed 决策者 Agent + 建群/团队路径绑定
3. 群设置 UI：改绑决策者、配置汇报对象
4. `group_workflow` 待确认时唤醒决策者；工具权限拆分（群主 vs 决策者）
5. 私聊求批桥接 + 汇报发送；决策者 / 群主 SOP 与 context 注入
6. 测试：常规自动确认+汇报、例外任一 manager 批准、汇报对象配置、`[]` 不发、无决策者时回退人类确认

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 误分类为 routine 放过财务/对外 | 关键词/规则预检 + `uncertain` 默认升级；高敏阶段可强制例外 |
| 汇报刷屏 | 文案极短；后续可做摘要开关（非首版） |
| 旧群无决策者 | 字段可空，行为与现网一致 |
| 求批与汇报混淆 | 文案模板与 `GroupDecisionRequest` 状态区分 |

## 验收标准

1. 新建执行群自动有决策者；设置可改绑。
2. 常规阶段待确认：决策者可确认推进，群主不能确认。
3. 例外类别：manager 私聊收到求批；任一批准后推进。
4. 每次拍板（含拒绝定稿）按配置向汇报对象发简短私聊；默认全部 manager；`[]` 不发。
5. 人类 manager 手动确认仍可用；无决策者的群行为不变。

## 开放后续（非首版）

- 汇报日汇总代替逐条
- 多级审批 / 指定财务审批人
- 决策看板与审计导出
