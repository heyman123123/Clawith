# 团队搭建：创建前确认工作流（模板底 + AI 可改）

**Status:** approved  
**Date:** 2026-07-31  
**Depends on:** 现有 Team Builder（成员草案 / provision）

## 问题

建群后工作流常与目标不匹配；成员可在确认页改，但工作流只能建群后改，闭环晚、返工多。

## 目标

创建群之前，人必须确认并可调整：

1. 团队成员（角色/职责）
2. 工作流（阶段、是否决策者审批门、目标摘要）

不满意可用自然语言让 AI 改，再确认创建。

## 决策

| 项 | 选择 |
|----|------|
| 工作流底稿 | **C**：固定模板为底 + AI 可改 |
| 模板 | 协作推进 / 敏捷 / 产研；默认协作推进 |
| 创建后 | 仍可用群内「工作流管理」替换 |

## 流程

```text
① 填目标 → 生成团队草案（成员）
② 选工作流底稿（default / agile / product_research）
③ 同页展示：成员卡 + 阶段时间线（标题/目标/是否需决策者审批）
④ 可手工改成员；可「让 AI 调整」：反馈 → 更新成员和/或工作流
⑤ 确认创建 → provision：建群 + seed 决策者 + 按确认的 workflow plan 创建工作流
```

## 数据

在 `TeamBuildDraft.reviewed_plan`（及 generated）增加：

```json
"workflow": {
  "preset": "default|agile|product_research|custom",
  "name": "协作推进",
  "stages": [
    { "key": "clarify", "title": "澄清目标", "goal": "...", "requires_approval": false },
    { "key": "decompose", "title": "拆解工作", "goal": "...", "requires_approval": true }
  ]
}
```

- 初次：按所选 preset 填阶段；AI 改写后 `preset` 可为 `custom`
- 确认创建：`create_workflow(..., plan=validated)`，与成员一并生效（不再忽略草案、硬编码 default）

## AI 调整

- 确认页「调整方案」文本框（可指定只改成员 / 只改流程 / 都改）
- 后端基于当前 `reviewed_plan` + 反馈再生成，写回并 `plan_version++`
- 校验：≥1 leader；阶段 ≥2；`key` 唯一；审批阶段有 goal

## UI

- 上：成员列表（可改）
- 中：工作流阶段列表 + preset 下拉（切换底稿会重置阶段，可再 AI 改）
- 下：调整反馈 +「让 AI 改一版」+「确认创建」

## 边界

- 创建前不启动群主/跑任务
- 决策者仍自动 seed；审批门仍由决策者拍板

## 验收

1. 创建前可见并可改工作流阶段与审批门  
2. 切换模板重置阶段；AI 反馈可改成员和/或流程  
3. 创建后群工作流与确认草案一致  
