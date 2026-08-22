# Intent-Driven Project Orchestrator — 未完成需求文档（POD）

> **状态**: 🟡 DRAFT（未完成，待用户过目）
> **生成日期**: 2026-08-22
> **目标读者**: 项目所有者 + 后续 AI Coding Agent
> **范围**: Intent-Driven Project Orchestrator 模块（不含 Clawith 其他子系统）
> **依据**: 项目实际代码（backend + frontend）+ 已有 `docs/superpowers/specs/2026-08-21-intent-driven-project-orchestrator-design.md` + 当前未提交修改

---

## 1. 文档目的

这是一份**未完成的需求文档**（POD, Product Overview Document），目的是：

1. 把「Intent-Driven Project Orchestrator」从用户输入到执行的全链路梳理清楚，让任何读者（人或 AI）只看这一组文档就能正确地：
   - 修改相关代码
   - 修复 bug
   - 设计新功能
   - 评估影响面
2. 明确当前**未完成 / 已知 bug** 的状态，方便你确认修复优先级。

> 本文档不是要取代 `docs/superpowers/specs/...` 或 `docs/constitution.md`。后者是**架构宪法与已通过的设计**，本文档是**需求侧快照 + 现状盘点**。

---

## 2. 文件索引

| # | 文件 | 内容摘要 | 状态 |
|---|---|---|---|
| 00 | `00-README.md` | 本文件：索引 + 状态 + 阅读路径 | 🟡 DRAFT |
| 01 | [`01-product-overview.md`](./01-product-overview.md) | 产品定位、用户故事、业务范围、术语表 | ⬜ 待写 |
| 02 | [`02-architecture-and-runtime.md`](./02-architecture-and-runtime.md) | 系统架构图、四类事实分离、C1-C6 映射 | ⬜ 待写 |
| 03 | [`03-state-machines.md`](./03-state-machines.md) | Draft / TaskCard / ChiefRun / Group 四类状态机 | ⬜ 待写 |
| 04 | [`04-flow-and-sequences.md`](./04-flow-and-sequences.md) | 端到端流程图、序列图、活动图 | ⬜ 待写 |
| 05 | [`05-data-dictionary.md`](./05-data-dictionary.md) | 新增/扩展表、字段、索引、枚举、JSON 结构 | ⬜ 待写 |
| 06 | [`06-api-and-contracts.md`](./06-api-and-contracts.md) | REST 接口契约、错误码、幂等性 | ⬜ 待写 |
| 07 | [`07-permission-matrix.md`](./07-permission-matrix.md) | 角色 × 资源 × 操作 矩阵 | ⬜ 待写 |
| 08 | [`08-pages-and-interactions.md`](./08-pages-and-interactions.md) | 页面、组件、控件、路由、交互方式 | ⬜ 待写 |
| 09 | [`09-exceptions-and-boundaries.md`](./09-exceptions-and-boundaries.md) | 异常情况、边界条件、降级策略 | ⬜ 待写 |
| 10 | [`10-known-bugs-and-todo.md`](./10-known-bugs-and-todo.md) | 从 git diff + 代码推断的 Bug 清单 | ⬜ 待写 |

> 每章末尾会追加 `### 已知 Bug / 待修复` 小节，集中列出该章节涉及的具体 bug。

---

## 3. 阅读路径（按角色）

### 3.1 如果你是「AI Coding Agent」，准备修 bug
1. 先读 `10-known-bugs-and-todo.md`，锁定 bug 编号。
2. 跳到对应章节（03 / 04 / 05 / 06 / 08）的「已知 Bug」小节，看症状 + 推测根因。
3. 按章节给出的**接口契约 / 数据字典 / 状态机**定位修改点。
4. 修完后对照 `09-exceptions-and-boundaries.md` 检查边界。

### 3.2 如果你是「产品 / 项目所有者」
1. 先读 `01-product-overview.md` 确认业务范围。
2. 读 `04-flow-and-sequences.md` 看端到端体验。
3. 读 `08-pages-and-interactions.md` 看 UI 路径。
4. 看 `10-known-bugs-and-todo.md` 决定修复顺序。

### 3.3 如果你是「新人开发者」
按 00 → 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 顺序通读。

---

## 4. 当前未提交修改概览（来自 `git diff --stat`）

```
 backend/app/api/groups.py                     | 101 ++++-----
 backend/app/api/orchestrator.py               | 290 ++++++++++++--------------
 backend/app/database.py                       |  15 +-
 backend/app/services/group_message_service.py |   1 +
 frontend/src/pages/DraftPreview.tsx           |   2 +-
 frontend/src/pages/Projects.tsx               |   2 +-
 frontend/src/services/orchestrator.ts         |   5 +-
 7 files changed, 206 insertions(+), 210 deletions(-)
```

涉及的关键点（在后续章节展开）：
- **Backend**: `orchestrator.py` 大幅改动（路由重组 + LLM 解析 + 模型解析重构）；`groups.py` 调整；`database.py` 数据库引擎改动；`group_message_service.py` 微调。
- **Frontend**: `Projects.tsx` 与 `DraftPreview.tsx` 各 1 行改动（很可能是修复 React Hook 顺序或空引用）；`orchestrator.ts` service 接口微调。

> 详细 bug 见 `10-known-bugs-and-todo.md`。

---

## 5. 文档未完成项

- 🟡 各章节细节待补充（特别是 03 状态机、05 数据字典、08 页面交互，需要逐表 / 逐页面核对）
- 🟡 Bug 清单中部分「推测根因」需要你确认后才能定级
- 🟡 权限矩阵中「跨租户拒绝」与「群内自我隔离」的具体实现细节未在源码中直接验证
- ⬜ 没有正式的实施计划（实施计划已在 `docs/superpowers/plans/2026-08-21-intent-driven-project-orchestrator.md` 中，本文档不重复）

---

## 6. 关联文档

| 文档 | 路径 | 关系 |
|---|---|---|
| 项目宪法 | `docs/constitution.md` | C1-C6 不可变原则 |
| 架构总览 | `docs/architecture/01-architecture-overview.md` | 整体拓扑 |
| Runtime 边界 | `docs/architecture/02-backend-runtime-boundary.md` | C1 执行边界 |
| 多租户数据 | `docs/architecture/03-multi-tenant-data-model.md` | C2 强制作用域 |
| Orchestrator 数据模型 | `docs/architecture/04-orchestrator-tables.md` | 新增/扩展表 |
| Orchestrator 设计 spec | `docs/superpowers/specs/2026-08-21-intent-driven-project-orchestrator-design.md` | 已通过的设计 |
| Orchestrator 实施计划 | `docs/superpowers/plans/2026-08-21-intent-driven-project-orchestrator.md` | 已有的实施步骤 |

---

## 7. 已知 Bug / 待修复

> 本节为本文档自身的待办，不替代 `10-known-bugs-and-todo.md`。

- 🟡 **BUG-POD-001**: 文档为「未完成」状态，部分章节（01/02/05/06/07/08/09/10）尚未撰写。
- 🟡 **BUG-POD-002**: Bug 清单的「推测根因」需要你确认。
- 🟡 **BUG-POD-003**: 文档与 `docs/constitution.md` 的 C1-C6 是否完全对齐，未做最终一致性检查（计划在 self-review 时完成）。