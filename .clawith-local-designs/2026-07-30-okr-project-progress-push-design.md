# OKR 按项目进度周期推动 — 设计

日期：2026-07-30  
状态：已定稿（待实现）  
分支：`feat/add-flow`（或后续功能分支）

## 背景与问题

公司 OKR 当前由租户级日历 cron 驱动（日报收集、日/周/月报），与群工作流（项目进度阶段）零耦合。业务需要：在企业设置中可选「按项目进度周期」推动 OKR，并与日历模式并存、可按群排除。

## 目标

1. **推动节奏可选**：仅日历 / 仅项目进度 / 两者并存（默认两者并存）。
2. **群范围**：默认自动纳入「有群工作流的执行群」；企业设置可排除个别群。
3. **触发节点可多选**：阶段激活、阶段完成、需审批、工作流完成；默认勾选「阶段完成 + 工作流完成」。
4. **推动动作**：对该群相关成员发起 OKR 进度收集，并预填阶段摘要/证据；成员可改后提交。
5. **去重**：同一人同一自然日只催一次（日历与项目节点共享去重）。

## 非目标

- 证据/阶段自动写入 KR 数值（留后续）
- 用 OKR 收集驱动或阻塞群阶段推进
- 重做 OKR 季度/月目标模型
- 合并群工作流 `daily_digest` 与 OKR 收集为同一条消息
- 改造公司日/周/月报的生成节奏（`workflow` 模式下报表仍可按日历跑）

## 需求决策记录

| 项 | 选择 |
|----|------|
| 能力形态 | C：日历 + 项目阶段并存，可按群绑定 |
| 群范围 | C：默认自动跟踪有工作流的执行群，可排除 |
| 触发节点 | C：可多选；默认阶段完成 + 工作流完成 |
| 推动动作 | B：收集 + 预填阶段摘要/证据 |
| 去重 | A：同一人同一天只催一次 |

## 方案

采用 **设置扩展 + 工作流事件桥接**（方案 1）：

- 扩展 `OKRSettings` 与 `OkrTab`
- 在群工作流关键状态点调用 `okr_workflow_bridge`
- 复用 `okr_daily_collection` 发信/A2A，增加按群子集、预填上下文、人日去重

不采用：每群独立 cron、或仅 @OKR Agent 由 LLM 决定催谁。

## 数据模型

`OKRSettings`（每租户一行）新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `push_cadence` | string | `calendar` \| `workflow` \| `both`，默认 `both` |
| `workflow_trigger_events` | JSON 数组 | 元素为事件键，见下表；默认 `["stage_completed","workflow_completed"]` |
| `excluded_group_ids` | JSON 数组 | UUID 字符串列表；这些群不参与项目进度推动 |

### 事件键与工作流挂点

| 设置键 | 工作流时机 |
|--------|-----------|
| `stage_activated` | 管理员确认后激活下一阶段时 |
| `stage_completed` | 管理员点「确认并进入下一阶段」后（`confirm_stage`） |
| `approval_required` | 证据齐套进入待确认（可选催办，不算「已到达」） |
| `workflow_completed` | 最后一阶段经人工确认完成后 |

**关键约束：**「阶段到达勾选节点」以**人工确认**为准，不以证据齐套自动完成为准。开启项目进度 OKR 推动时，阶段齐套后会进入待确认，需群管理确认后才推进并触发 OKR 到达类事件。

### 群是否纳入

群被纳入项目进度推动，当且仅当：

1. `push_cadence` ∈ `{workflow, both}`
2. 该群存在未删除的 `GroupWorkflow`（或当前 active/awaiting_approval/刚完成触发点上的 workflow）
3. `group_id` 不在 `excluded_group_ids`

「执行群」首版操作定义：**租户内带 `GroupWorkflow` 的群**（与 Projects 建出的执行群一致的主路径）；不单独再维护「执行群」标记，除非后续产品要求收紧。

## 行为设计

### 日历模式

- `calendar` / `both`：现有日报收集 cron 与 `_sync_okr_report_triggers` 行为保留（收集时间、跳过非工作日等）。
- `workflow`：关闭「成员日报收集」类 cron；公司日/周/月报默认仍按日历生成（无新料则空/跳过），避免拆报表链路。

### 项目节点触发

1. 工作流到达挂点 → 调用 `okr_workflow_bridge.on_workflow_event(...)`
2. 桥接读取 settings；校验 cadence、事件是否勾选、群是否排除
3. 解析群成员（human + agent Participant）→ 映射为 OKR 收集对象（与现有关系网/member 身份对齐；无法映射则跳过并打日志）
4. 对每个目标成员尝试发起收集：
   - 构造预填上下文：群名、阶段标题/目标、近期完成与阻塞工作项摘要、关键 evidence 摘要（截断）
   - `source=workflow_stage`，附带 `group_id` / `workflow_id` / `stage_id` / `event_key`
5. **去重**：键为 `tenant_id + member_identity + report_date`（时区与现有 OKR 日报日切一致）
   - 当日已催过且尚未回复：不重复发催收；可将新阶段摘要追加到已有收集上下文（若实现成本低；否则仅跳过）
   - 当日已提交日报：不再催

### 与群主 SOP 的边界

- 桥接只发起 OKR **汇报收集**，不创建新的「推进阶段」指令
- 不替代 `leader_action` / 日统计日报；三者可并存但职责分离

## API / UI

### API

- `GET/PUT /api/okr/settings`：读写新字段；PUT 时按 `push_cadence` 同步触发器（`_sync_okr_report_triggers`）
- 可选：`GET /api/okr/settings/workflow-groups` 列出可选排除的群（id、name、是否有 workflow），供 UI 多选；若现有 groups 列表 API 已够用则可复用

### UI（`OkrTab`）

1. 推动节奏：三选一
2. 项目触发节点：多选 checkbox（仅 cadence 含 workflow 时启用）
3. 排除的群：多选（仅 cadence 含 workflow 时启用）
4. 文案说明：项目节点推动 = 汇报收集，不会用 OKR 去推进群阶段；同一人同一天只催一次

## 实现落点

| 区域 | 改动 |
|------|------|
| `backend/app/models/okr.py` | 新字段 |
| Alembic migration | 默认值与类型 |
| `backend/app/api/okr.py` | settings schema + sync triggers |
| `backend/app/services/okr_workflow_bridge.py` | **新建**桥接 |
| `backend/app/services/group_workflow/service.py` | 挂点调用桥接（失败只记日志，不回滚工作流事务） |
| `backend/app/services/okr_daily_collection.py` | 按群子集、预填、去重 |
| `frontend/.../OkrTab.tsx` + i18n | 设置 UI |
| 测试 | 节点触发 / 排除群 / 去重 / workflow 关收集 cron |

桥接调用约定：在工作流状态已成功提交后触发（同事务末或 `await` 后 best-effort），**桥接失败不得导致阶段回滚**。

## 测试要点

- `both` + 默认节点：阶段完成触发收集；未勾选的「阶段激活」不触发
- 排除群：事件发生但不收集
- 去重：同日先日历后阶段（或反之）只催一次
- `workflow`：成员收集 cron 关闭；桥接仍可用
- `calendar`：桥接 no-op
- 无法映射的成员被跳过且不影响其他人
- 预填内容含阶段标题且有长度上限

## 风险与后续

- 成员映射不全时收集覆盖偏窄 → 日志可观测，后续可补「自动建关系」
- KR 自动更新未做 → 产品上仍靠成员确认后的日报/手动改进度
- 多群同日多阶段：去重后可能只看到第一次预填 → 优先做「追加摘要」为增强项

## 验收标准

1. 管理员可在企业设置切换三种节奏、配置节点与排除群并保存生效。
2. 未排除的工作流群在默认节点上会触发 OKR 收集，消息含阶段预填信息。
3. 同一成员同一天不会收到第二次催收。
4. 群阶段推进不因 OKR 桥接失败而中断。
