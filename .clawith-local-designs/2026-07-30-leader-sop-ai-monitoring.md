# 群主 SOP 敏捷推进 + 群/Agent 维度 AI 监控

## §1 群主敏捷推进

- 只靠 SOP 事件推进；审批闸保留；需人确认时立刻 @ 人类并继续催证据
- `worker` 按 `kind` 唤醒文案；payload 带 `confirm_targets`
- `group_context_builder` 强化群主 instruction（禁心跳/定时等待）
- 日统计日报：每天一次 @ 群主，仅确认、不驱动阶段

## §2 AI 监控

- 仪表盘主视图按**群**汇总，再下钻 Agent → 调用 → 详情
- 群侧面板同思路：本群 Agent 汇总 → 调用 → 详情
- 支持排序、日/24h、刷新
