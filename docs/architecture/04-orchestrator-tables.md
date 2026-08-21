# 04 — Intent-Driven Orchestrator: Data Model & Runtime

> Status: Implementation baseline (Wave 1-7 of feat/001-intent-driven-orchestrator).

## 1. New Tables (5)

| Table | Purpose | C1/C2/C3/C5 Notes |
|---|---|---|
| `drafts` | LLM-generated proposals awaiting user approval | C2 tenant_id NOT NULL; C5 no FK; idempotency key |
| `task_cards` | Kanban cards (Group board or Agent personal board) | C2 + C5; `version` column for optimistic lock |
| `task_card_dependencies` | DAG edges between cards | C2 + C5; UNIQUE pair prevents duplicate edges |
| `task_board_events` | Event log consumed by Chief Runtime via PG LISTEN | C2 + C5; composite index (tenant_id, group_id, occurred_at DESC) |
| `chief_runs` | Long-lived Chief Runtime lifecycle metadata | C2 + C5; UNIQUE group_id (1:1 with groups) |

## 2. Extended Tables (2)

- `agent_templates` — adds `template_visibility`, `tenant_id`, `approval_state`, `rejected_reason`
- `chat_sessions` — adds nullable `chief_run_id` (Chief 1:1 Chat marker)

## 3. New Runtime: Chief (run_kind="orchestrator")

```
┌────────────────────────────────────────────┐
│ EventListener (PG NOTIFY + 30s compensation)│
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ Reasoner (LLM-driven ActionPlan)           │
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ ActionExecutor (TaskBoardService +         │
│                 RuntimeCommandIntake)      │
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ OrchestratorRunLoop (long-lived daemon)    │
│  - FAILURE_THRESHOLD = 5 → degraded         │
│  - COOLDOWN_SECONDS = 300 → retry           │
│  - status=stopped → exit                    │
└────────────────────────────────────────────┘
```

## 4. C1 Compliance Checklist

- [x] `RuntimeCommandIntake` is the only entry point for starting a Chief Run
- [x] Chief does not call LangGraph nodes directly
- [x] `chief_runs` is metadata only; LangGraph Checkpoint is single source of truth
- [x] AtomicCreator persists Draft with `status='consumed'` after success (idempotency)
- [x] Reconciliation job treats Checkpoint state as authoritative (C3)

## 5. Where to Look in Code

| Concern | Path |
|---|---|
| Draft proposal LLM flow | `backend/app/services/orchestrator/{intent_analyzer,agent_selector,group_planner}.py` |
| Draft persistence | `backend/app/services/orchestrator/orchestrator_service.py` |
| Draft → Group materialization | `backend/app/services/orchestrator/atomic_creator.py` |
| Task Board CRUD | `backend/app/services/task_board/{service,dao,event_publisher,column_defs}.py` |
| Chief Runtime | `backend/app/services/agent_runtime/orchestrator/{orchestrator_run,event_listener,reasoner,action_executor,checkpoint_state,command_handler}.py` |
| Template Registry | `backend/app/services/agent_template/registry/{visibility,approval,llm_generator}.py` |
| Frontend UI | `frontend/src/{services,pages/projects,components/Settings}/` |
| API routers (NOT auto-wired) | `backend/app/api/{orchestrator,task_board,template_registry}.py` |
