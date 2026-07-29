# HR 组队表单向导设计

日期：2026-07-30  
状态：已定稿（待实现）  
分支：`feat/modify-flow`

## 背景与问题

当前「需求与执行」主路径是：进入 HR 评审群 → 聊天描述需求 → 依赖 Agent 吐 JSON 才渲染方案卡 → 确认后 provision，并自动向执行群注入「请现在启动团队」文案。

问题：

1. 入口不应是「去 HR 群聊天」，而应是表单/按钮流程。
2. 「创建团队」与「启动团队」混在一起；启动只是系统文案，不是可编辑、可点击的交互。
3. 启动应以当前用户身份 `@群主` 发送适合需求的启动文案。

## 目标

在 `/projects` 提供四步点击向导：

**填写需求 → 生成方案 → 确认创建 → 启动**

- 生成方案：一键直调 LLM（`POST /projects/team-plans`），不进 HR 群辩论。
- 确认创建：provision 智能体与执行群，**不自动发 kickoff**。
- 启动：可编辑文案；主按钮「生成并发送」→ 以用户身份 `@群主` 发到执行群 → 跳转执行群。
- 去掉 Projects 页「去 HR 群提需求」主入口（完全移除该 CTA）。

## 非目标

- Session B（governance_topup）闭环
- 恢复 / 保留 HR 群作为组队主入口
- 多 Agent 深度评审模式（后台辩论）
- 重构股东群决策流程

## 用户流程

### 页面

- 路由：`/projects`
- 主区域：四步向导（同页 step 切换）
- 次要：股东群入口可保留（与组队无关）
- 列表：「执行中的需求」保留；未完成 kickoff 的项目可「继续启动」回到第 4 步

### 步骤

| Step | 名称 | 用户操作 | 系统行为 |
|------|------|----------|----------|
| 1 | 填写需求 | 项目名 + 需求描述 →「生成方案」 | `POST /projects/team-plans` |
| 2 | 确认方案 | 方案卡片「查看详情 / 确认此方案」 | 选中 proposal，进入创建摘要 |
| 3 | 确认创建 | 「创建团队」 | select/provision，`send_kickoff=False` |
| 4 | 启动 | 编辑文案；「重新生成文案」；「生成并发送」 | draft（按需）→ send → 跳转执行群 |

### 前端状态机

`draft → proposals_ready → proposal_selected → provisioned → kickoff_sent`

## 后端设计

### 复用

- `POST /api/projects/team-plans`：打开 team_building session + 一键生成 ≥3 proposals
- `POST /api/hr-review/sessions/{id}/select`：选方案并 provision（向导主路径）  
  - 调用链需传入 `send_kickoff=False`

### 修改：`provision_team_from_plan`

增加参数：`send_kickoff: bool = True`

- `False`：创建 Agent、执行群、决策群、workflow；**不**调用 `enqueue_group_message` 发 wakeup
- `True`：保持现有自动 kickoff 行为（兼容旧调用方）
- 向导主路径一律 `False`

### 新增：kickoff API（挂在 `/api/projects`）

#### `POST /projects/{workflow_id}/kickoff/draft`

- 权限：当前用户租户内的该 workflow
- 可选 body：`instructions`（微调生成意图）
- 响应：
  - `content`：启动文案草稿
  - `leader_participant_id` / `leader_name`
  - `group_id` / `session_id`
- 生成策略：LLM 基于 `requirements`、roles、群主信息生成适合该需求的启动指令；失败回退 `build_team_wakeup_message` 模板

#### `POST /projects/{workflow_id}/kickoff/send`

- Body：`content`（用户最终文案，必填）
- 行为：
  - 解析当前用户在执行群的 human participant
  - `enqueue_group_message`：`sender` = 用户，`mention_participant_ids` = `[群主]`，正文为用户文案（含 `@群主名` 与现有 mention 协议一致）
  - 写入 `workflow.kickoff_sent_at`
- 幂等：若已 kickoff，返回 HTTP 200 + 已有结果，并带 `already_sent: true`（不重复发消息）
- 响应：`group_id`、`session_id`、`message_id`、`already_sent`

### 数据模型

`project_workflows` 增加可空字段：

- `kickoff_sent_at`（timestamptz，nullable）

用于「继续启动」、按钮禁用与 send 幂等。  
`ProjectOut` / 列表接口需暴露 `kickoff_sent_at`（或布尔 `kickoff_sent`）给前端。

### HR 评审群

- 后端仍可 `ensureBoard` / 挂 session，供 `/team-plans` 使用
- 前端 Projects **不再**展示「去 HR 群提需求」，也不再把用户导向 HR 群组队

## 前端设计

### `Projects.tsx`

替换为向导 UI：

1. 表单（name、requirements）
2. 复用 `HrProposalCard` / `HrProposalModal` 展示与确认
3. 创建确认摘要 +「创建团队」
4. textarea +「重新生成文案」+ 主按钮「生成并发送」

接入：

- `projectApi.buildTeamPlan`
- `hrReviewApi.selectProposal`（或封装为向导 API），创建后**停在第 4 步**，不立即 navigate
- 新：`projectApi.kickoffDraft` / `projectApi.kickoffSend`

### 第 4 步按钮语义（选项 C）

- 进入第 4 步：自动调用一次 `draft` 填入 textarea（可编辑）
- 「重新生成文案」：只调 `draft`，不发送
- 「生成并发送」：用**当前 textarea 内容**调 `send`；若内容为空则先 `draft` 再 `send`
- 成功后：禁用按钮，navigate 到 `/groups/{group_id}/{session_id}`

### 列表「继续启动」

当 `workflow.status === 'active'` 且 `kickoff_sent_at == null` 时，展示「继续启动」，恢复第 4 步上下文（带 `workflow_id`）。

## 错误处理

| 场景 | 行为 |
|------|------|
| 生成方案失败 | 留在 Step 1，保留表单，可重试 |
| proposals < 3 | 不允许进入 Step 2 |
| 创建失败 | 留在 Step 3，可重试 |
| draft 失败 | 回退模板文案，仍可编辑发送 |
| send 失败 | 留在 Step 4，保留文案，可重试 |
| 重复 send | 200 + `already_sent`；前端跳转执行群并禁用按钮 |
| 跨租户 | 403/404 |

## 测试计划

### 后端

- `send_kickoff=False` 不产生群消息；`True` 仍产生
- `kickoff/draft` 返回 content + leader 信息；LLM 失败走模板
- `kickoff/send` 以用户身份发送且 mention 群主
- 重复 send 返回 `already_sent` 且不重复落消息
- 租户隔离
- 列表/详情暴露 `kickoff_sent_at`

### 前端

- 四步状态流转
- 无「去 HR 群提需求」主 CTA
- 创建后不自动跳转；send 成功后跳转
- 「继续启动」恢复 Step 4

## 实现顺序建议

1. DB：`kickoff_sent_at` + migration
2. `provision_team_from_plan(send_kickoff=...)` + select/create 向导路径传 False
3. kickoff draft/send API + 测试
4. Projects 向导 UI + projectApi 封装
5. 去掉 HR 群主入口；列表「继续启动」
6. 端到端回归

## 决策记录

- 生成方案：一键 LLM（A），不进 HR 群辩论
- 启动交互：主按钮「生成并发送」（C）；进入 Step 4 预填可编辑草稿
- 旧 HR 群入口：完全移除（A）
- 总体方案：Projects 四步向导 + 复用 team-plans/select + 拆开创建与启动（方案 1）
