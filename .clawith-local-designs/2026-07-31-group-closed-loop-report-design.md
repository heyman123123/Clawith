# 群内闭环汇报：成员 → 群主 → 决策者 → 群主

## 决策者跨空间授权（2026-07-31）

创建/改绑/懒加载决策者时默认写入：
- `autonomy_policy.allow_group_cross_space = true`（群 Run 内可走 `send_platform_message` 等跨空间工具）
- `AgentPermission`：创建者 `manage` + 群人类 manager（及显式汇报对象）`use`

打开推进 tab 时也会回填已有决策者的上述授权。


## 补充修复（阶段卡住 / 群主找 admin）

- 去掉 OKR「项目推动」对**无审批阶段**的强制 `awaiting_approval`（OKR 只催收集，不挡推进）。
- 打开推进 tab 时：若卡在「无审批却 awaiting_approval」的遗留闸门，自动完成或退回 active。
- 团队群主 soul / 激活文案：禁止等人类拍板；计划就绪后必须 `submit_evidence`。
- 群主 runtime SOP：禁止向 admin 求项目批准；阶段激活后立刻交证据工具推进。


## 问题

成员完成工作项或需要决策时，不会主动找群主；阶段未齐时系统也不唤醒群主。决策拍板后群主缺少明确唤醒。群内未形成闭环。

## 目标链路（双保险）

```text
成员完成 / 需决策
  → 硬：系统 leader_action 唤醒群主
  → 软：成员公开 @群主 简短汇报（先 at 再写 @）
群主
  → 编排/催办；需项目拍板 → @决策者（不找人类做项目决策）
决策者拍板（routine / 例外升级人类 manager）
  → 硬：leader_action(kind=decision_resolved) 唤醒群主
  → 软：决策者公开 @群主 告知结论
群主按结论继续分派
```

## 角色

| 角色 | 做 | 不做 |
|------|----|------|
| 成员 | 交证据/阻塞后立刻向**群主**汇报；需决策只找群主 | 不直接找决策者/人类做项目拍板 |
| 群主 | 收汇报、编排；需拍板 @决策者 | 不 `confirm_stage`；不把项目拍板推人类 |
| 决策者 | 分类拍板；私聊汇报管理员（既有）；**群内 @群主** | 不取代群主编排 |

## 系统硬点

1. **`submit_evidence`**：单项变为 `done` 时，若阶段尚未齐备审批门，enqueue `leader_action(kind=member_progress)`（payload：stage/item/actor 摘要）。阶段齐备仍走现有 `approval_required` + `decision_action`（此时不必再单独 member_progress，避免双响；齐备路径的 `approval_required` 已唤醒群主）。
2. **`set_blocked`**：保持现有 `blocker` 唤醒。
3. **决策终态**（`auto_applied` / `approved` / `rejected`）：在确认/拒绝落库后 enqueue `leader_action(kind=decision_resolved)`，payload 含 title/status/summary；worker 文案要求群主按结论继续编排。
4. **SOP/context**：
   - 成员：强制完成后/需决策时 `@群主`；禁止直接找决策者做项目拍板。
   - 群主：收成员汇报后必要时转决策者；`decision_resolved` 时按结论分派。
   - 决策者：拍板后必须 `@群主` 公开告知（与私聊管理员汇报并存）。

## 边界

- 不改变决策者例外升级人类 manager 的路径。
- 成员不直接 `decision_action` 唤醒决策者。
- 私聊决策汇报（`decision_report_participant_ids`）保留。

## 验收

1. 成员提交证据且同阶段仍有未完成项 → 群主被唤醒（member_progress）。
2. 成员指令含「向群主汇报 / 先 at」。
3. 决策者确认或拒绝后 → 群主被唤醒（decision_resolved），文案含结论。
4. 决策者 SOP 要求拍板后 @群主。
