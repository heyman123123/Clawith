# Changelog

All notable changes to Clawith × Agency Orchestrator integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — P8 Defaults & UI Polish

### Added

* `backend/app/services/llm/default_propagation.py` —
  `propagate_tenant_default_to_unassigned_agents(tenant_id)` 回填 `Agent.primary_model_id IS NULL`
  的 agent 为该租户的 `Tenant.default_model_id`；内部 helper 保证 default 指向**启用且未删除**的
  LLMModel,否则安全返回 0 不写库。
  `propagate_tenant_default_all_tenants()` 全 tenant 扫描,返回 `{tenant_id: count}`。
* `backend/app/api/enterprise.py` — `POST /api/enterprise/llm-models/propagate-default`
  admin 路由(可选 `tenant_id`),返回 `{applied, tenants}`。
  `set_default_llm_model` 在原有 `previous_default → new_default` 迁移之上**额外**调用
  回填,让从未配过模型的 agent 也跟随新 default。
* `backend/app/main.py` — `lifespan` 在 governance backfill 段后 best-effort
  跑一次 `propagate_tenant_default_all_tenants`,失败仅 `logger.warning` 不阻断 startup。
* `frontend/src/components/UI/{EmptyState,LoadingState,ErrorBanner,PageHeader,ThemeToggle}.tsx`
  + `frontend/src/components/UI/index.ts` barrel — 统一 loading/empty/error 体验,
  使用 CSS 变量(`var(--bg-elevated)`/`var(--text-secondary)`)自动适配 light/dark。
* `frontend/src/hooks/useTheme.ts` — `{theme, toggle, setTheme}` + 跨标签 `storage` 同步,
  持久化到 `localStorage.theme`。

### Changed

* `frontend/src/pages/SkillMarket.tsx` / `MetricsDashboard.tsx` /
  `DeliveryReviewCenter.tsx` / `OfficialTemplates.tsx` / `AssetBrowser.tsx` —
  临时 `setSuccessMsg/setError` 替换为 `useToast()`;loading/empty/error 改用正式组件;
  卡片背景改 CSS 变量,支持深色模式。
* `frontend/src/pages/AssetBrowser.tsx` — **关键修复**:移除第 57-77 行硬编码
  `fetch('/api/ao/workflows/...')`(API 不存在),改走 `deliveryReviewApi.listRounds`,
  404/失败 → `.catch(() => [])` 优雅降级,EmptyState 引导用户。
* `frontend/src/pages/Layout.tsx` — 本地硬编码 `/api` 的 `fetchJson` 替换为从
  `services/api` 导入的统一封装;顶部栏接入 `<ThemeToggle />` 切换深浅模式;
  移除本地 theme state,改用 `useTheme()` hook(单数据源 + 跨标签同步)。

### Tests

* `backend/tests/test_default_model_propagation.py` — 6 个 pytest-asyncio case:
  no-default / unassigned backfill / 已有值不动 / soft-deleted 跳过 /
  default model 失效 / 全 tenant 聚合。全 pass。

## [Unreleased] — P7 Hardening

### Fixed

* `backend/alembic/versions/202607275000_add_delivery_review_and_human_queue.py` —
  renamed revision id from `add_delivery_review_and_human_queue` (35 chars,
  longer than `alembic_version.version_num VARCHAR(32)`) to `add_delivery_review`
  (19 chars) so `alembic upgrade head` can stamp it.
* `backend/alembic/versions/202607275000_add_delivery_review_and_human_queue.py` +
  `backend/app/models/delivery_review.py` — `workflow_delivery_approvals.workflow_id`
  and `workflow_human_reviews.workflow_id` foreign keys now reference
  `project_workflows.id` instead of the reserved `workflow_runs` table.
* `backend/app/main.py` — official workflow template seeder iterates over
  existing tenants (was a single global tenant_id=NULL seed that violated
  the NOT NULL constraint).

### Added

* `backend/app/services/ao/asset_directory_enforcer.py` — 8-bucket asset category
  enum (`00-工作流定义` … `07-历史迭代`) with backwards-compatible legacy
  mapping and an opt-in `CLAWITH_ASSET_DEBUG` filesystem assertion hook.
* `backend/app/services/workflow_template_seeder.py` — 30 official
  workflow templates (idempotent) and `seed_official_workflow_templates()`.
* `backend/app/services/delivery_scoring.py` — 2-dimension (60% quality /
  40% coverage) scoring rubric, ≥90 pass threshold, 3-round auto-loop cap.
* `backend/app/services/security_shell.py` — security helpers: SQL smell
  scan, tenant guard, safe subpath, audit categories, placeholder encryption.
* `backend/app/api/delivery_review.py` — REST endpoints for delivery
  rounds + the cross-cutting human review queue (审批卡 / 决策卡 /
  高危技能审核 / 质检异常人工复核).
* `backend/app/models/delivery_review.py` — `WorkflowDeliveryApproval` and
  `WorkflowHumanReview` SQLAlchemy models.
* `backend/alembic/versions/202607275000_add_delivery_review_and_human_queue.py`
  — migration that creates the two tables.
* `tests/test_ao_p7_hardening.py` — 25-case P7 hardening test suite.
* `docs/adr/0002-p7-hardening.md` — ADR for the P7 hardening decisions.
* `CHANGELOG.md` — this file.

### Changed

* `backend/app/main.py` — `lifespan` now installs the metrics cron in
  worker mode and seeds the 30 official templates at startup.
* `backend/alembic/env.py` — imports the new delivery-review models so
  Alembic autogenerate stays in sync.

### Fixed

* `backend/app/services/ao/asset_directory_enforcer.py` — `is_valid_category`
  now accepts the on-disk Chinese directory name, the Python enum member
  name, *and* the legacy 4-key enum.

## [P6] — Skill Self-Learning + Intelligence + Metrics — 2026-07-27

### Added

* Skill marketplace, sandbox execution, high-risk approval, and skill
  learning records.
* HR Top-3 template matching, daily metrics aggregation, and a
  five-level metrics dashboard.
* Nightly in-process metrics backfill cron.

## [P5] — Skills — 2026-07-27

### Added

* `SkillMarketListing`, `SkillSandboxRun`, `SkillApprovalRequest`,
  `SkillLearningRecord`, `AgentSkillBinding` models and APIs.
* `sandbox_risk.assess_risk` static code heuristics for skill safety
  classification.

## [P4] — Evolution — 2026-07-27

### Added

* `AgentEvolutionRecord`, `AgentRoleVersion`, `AgentEvolutionSignal`,
  `AgentEvolutionDraft`, `AgentHarnessFixture`, `AgentHarnessRun`.
* Patch engine + LLM judge integration with deterministic fallback to
  the rule engine.

## [P3] — Delivery & Quality Closure — 2026-07-27

### Added

* Quality officer + delivery coordinator roles and tool registrations.

## [P0–P2] — AO + Scheduler + Asset — 2026-07-27

### Added

* Agency Orchestrator CLI wrapper (`ao/`), scheduler (`scheduler_tools`),
  quality engine, asset writer, run repository.
* 4-way role separation (调度/执行/质控/交付) and project provisioning
  for AO YAML.
* Asset directory under `ao-output/<workflow_id>/` with README / summary /
  assets / feedback slots per stage.

## [Prerequisite] — Governance overhaul — 2026-07-27

### Added

* HR board + shareholder seed on tenant registration.
* HR proposal cards + confirm → execution group provisioning.
* Decision → shareholder escalation with Board Secretary owner.
* 需求入口迁回 HR 群（不再以「创建项目」为主入口）.
