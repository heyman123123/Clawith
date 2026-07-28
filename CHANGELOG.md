# Changelog

All notable changes to Clawith × Agency Orchestrator integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — P7 Hardening

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
