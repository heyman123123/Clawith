# OKR Project-Progress Push Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Let enterprise OKR settings choose calendar and/or group-workflow stage events to drive OKR progress collection, with auto-included workflow groups, exclusions, prefills, and same-person-same-day dedupe.

**Architecture:** Extend `OKRSettings`; on workflow stage lifecycle events call `okr_workflow_bridge` (best-effort); reuse `okr_daily_collection` with group member subset + prefill + dedupe keys.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, React/TS OkrTab

**Spec:** `.clawith-local-designs/2026-07-30-okr-project-progress-push-design.md`

## Global Constraints

- Bridge failures must not roll back workflow transitions
- Do not auto-update KR values from evidence
- Do not use OKR collection to advance workflow stages
- Default cadence `both`; default events `stage_completed`, `workflow_completed`
- Same member + report day: at most one outreach

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/models/okr.py` | New settings columns |
| `backend/alembic/versions/202607302200_okr_push_cadence.py` | Migration |
| `backend/app/services/okr_collection_dedupe.py` | Dedupe / outreach ledger helpers |
| `backend/app/services/okr_daily_collection.py` | Group-scoped + prefill collection entry |
| `backend/app/services/okr_workflow_bridge.py` | Settings gate + build prefill + invoke collection |
| `backend/app/services/group_workflow/service.py` | Call bridge at hooks |
| `backend/app/api/okr.py` | Schema + sync triggers for cadence |
| `backend/app/services/agent_seeder.py` | Align `_sync_okr_triggers_with_settings` with cadence |
| `frontend/.../OkrTab.tsx` + i18n | Settings UI |
| `backend/tests/test_okr_workflow_bridge.py` | Unit tests |

---

### Task 1: Settings model + migration + API schema

- [ ] Add `push_cadence`, `workflow_trigger_events`, `excluded_group_ids` to `OKRSettings`
- [ ] Alembic migration with defaults
- [ ] Extend `OKRSettingsOut` / `OKRSettingsUpdate` and PUT handler
- [ ] Sync: `workflow` cadence disables `daily_okr_collection` even if `daily_report_enabled`
- [ ] Tests for settings round-trip / trigger sync behavior

### Task 2: Collection entry for workflow push + dedupe

- [ ] Add outreach ledger or reuse MemberDailyReport existence + in-memory/DB marker for “already asked today”
- [ ] `collect_for_workflow_event(tenant_id, group_id, members, report_day, prefill)` 
- [ ] Prefill appended to human/agent request messages
- [ ] Skip if already submitted or already outreached today
- [ ] Unit tests for dedupe and prefill

### Task 3: Bridge + workflow hooks

- [ ] `okr_workflow_bridge.on_workflow_event(db, *, tenant_id, group_id, event_key, workflow, stage=None)`
- [ ] Gate on settings; resolve members; build stage/item/evidence summary
- [ ] Call from service after stage_activated / approval_required / stage complete / workflow complete
- [ ] Bridge errors logged only
- [ ] Tests with mocks

### Task 4: Frontend OkrTab

- [ ] Cadence radios, event checkboxes, exclude-group multi-select (list groups with workflows)
- [ ] i18n zh/en
- [ ] Disable workflow controls when cadence is `calendar`

### Task 5: Verification

- [ ] `pytest` targeted files green
- [ ] `ruff` + frontend `tsc` clean
