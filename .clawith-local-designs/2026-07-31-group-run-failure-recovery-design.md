# 群 Run 失败恢复：@群主 + 模型额度定时确认

**Status:** approved  
**Date:** 2026-07-31  

## 问题

群内任务失败（含 `model_call_failed` / Model provider request failed）后常静默结束，群主不知道、也不会被唤醒；额度类错误立刻重试无意义。

## 目标

1. **一般执行失败**：自动在群内 at+@群主，附失败摘要与 Run ID，由群主决定是否重试。  
2. **`model_call_failed`（及同类模型供应商/额度失败）**：每 **30 分钟**探测一次；探测认为可恢复后 **只 @群主**（不自动续跑），由群主决定是否重试。  
3. 默认定时窗口 **24 小时**；超时再 @群主说明已停止探测，并结束任务。

## 决策记录

| 项 | 选择 |
|----|------|
| 额度恢复后 | **只 @群主，不自动续跑** |
| 探测间隔 | 30 分钟 |
| 最长持续 | **24 小时**，超时 @群主并停止 |
| 一般失败 | 立刻 @群主（不去抖刷屏：同 run 同错误只唤醒一次） |

## 分类

| 类别 | 判定（示例） | 行为 |
|------|----------------|------|
| `model_quota` | `error_code == model_call_failed`；摘要含 provider/quota/rate limit 等 | 建/更新 resume job；30m 探测；成功或超时均 @群主 |
| `general` | 其他群 Run 失败 | 立刻 @群主一次 |

不自动 enqueue 新 Run；恢复文案须含 `Run ID`、失败码、失败 Agent，并明确「请决定是否重试」。

## 数据

新建（或等价）`group_run_resume_jobs`：

| 字段 | 说明 |
|------|------|
| `id` | UUID |
| `tenant_id` / `group_id` / `session_id` | 范围 |
| `failed_run_id` | 原 Run |
| `failed_agent_participant_id` | 失败执行者 |
| `error_code` / `error_summary` | 截断 |
| `kind` | `general` \| `model_quota` |
| `status` | `pending` \| `notified` \| `recovered_notified` \| `timed_out` \| `cancelled` |
| `next_check_at` | 下次探测 |
| `check_interval_seconds` | 默认 1800 |
| `expires_at` | 创建时 + 24h |
| `last_checked_at` / `check_count` | 审计 |
| `leader_notified_at` | 去重 |

唯一约束建议：`failed_run_id` 唯一（同一失败 Run 一条任务）。

## 流程

### 一般失败

1. 群 Run 终态失败且非 model_quota（或先归 general）  
2. 若尚无 job / 未 notified → 群消息 at+@群主 → `status=notified`

### 模型额度 / model_call_failed

1. 创建 job：`kind=model_quota`，`next_check_at=now+30m`，`expires_at=now+24h`  
2. **首次**可立刻 @群主一次：「模型调用失败，已安排每 30 分钟确认；请暂缓重试或稍后决定」  
3. Worker 扫描到期 `pending`：
   - 探测模型可用（同租户/同模型一次最小探测，或 provider 健康接口）  
   - **仍失败**：`next_check_at+=30m`，`check_count++`  
   - **可用**：@群主「模型已恢复，请决定是否重试 Run {id}」→ `recovered_notified`，停止探测  
   - **`now >= expires_at`**：@群主「24h 内未恢复，已停止自动确认」→ `timed_out`

### 探测原则

- 探测失败不得再开完整业务 Run  
- 探测成功 **禁止** 自动续跑原 Run

## 落点（实现时）

- 群 Run 失败钩子（runtime delivery / product reconciler / group message 完成路径）  
- `group_run_resume` service + worker（可挂现有 group workflow worker 旁路）  
- 唤醒复用群消息 + `at` 协议（先 at 再可见 @群主）

## 边界

- 非群会话不启用  
- 不替代人类改 key / 换模型的配置操作  
- 与决策者闭环无关；本需求只唤醒**群主**

## 验收

1. 一般失败 → 群主被 @ 一次，含 Run ID  
2. `model_call_failed` → 有 30m 探测任务；恢复后只 @群主、无自动新 Run  
3. 24h 超时 → @群主并停止；同 Run 不重复建 job  
