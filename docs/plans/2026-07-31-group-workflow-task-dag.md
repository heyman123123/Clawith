# Group Workflow Task DAG Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace message-driven group-workflow progression with an evidence-gated task DAG that dispatches only ready work to the assigned agent and never advances a stage without all required task completions.

**Architecture:** Keep workflow stages as ordered, non-skippable milestones and make `GroupWorkflowItem` the task node. Store edges and group-leader-confirmed task changes relationally; the service validates and materializes the DAG, while event-driven reconciliation unlocks only downstream nodes whose predecessors are accepted. The existing worker sends task-ready events to assignees and only sends exceptions, change requests, and stage summaries to the leader.

**Tech Stack:** FastAPI, SQLAlchemy async + PostgreSQL/Alembic, Pydantic v2, React 19 + TypeScript + Vite, existing group realtime/event worker, pytest and frontend TypeScript build.

**User constraint:** Work only in the current branch. Do not create commits, push, reset, checkout, or otherwise alter unrelated work.

---

### Task 1: Add failing contracts tests for task identifiers, acceptance, and DAG validation

**Files:**
- Create: `backend/tests/test_group_workflow_contracts.py`
- Modify: `backend/app/services/group_workflow/contracts.py`

**Step 1: Write failing plan-validation tests**

Add parameterized tests that construct a two-stage `WorkflowPlan` with task references in the canonical `<stage_key>.<item_key>` form. Cover a valid graph, a cycle, a dependency in a later stage, an unknown dependency, a self dependency, duplicate edges, missing task acceptance criteria, and a participant outside the group.

Also assert a task cannot list itself or duplicate an edge and that participant references are still checked.

**Step 2: Run the focused tests and verify failure**

Run: `rtk pytest backend/tests/test_group_workflow_contracts.py -q`

Expected: FAIL because `depends_on` and task-level acceptance criteria are not modeled or validated.

**Step 3: Extend Pydantic contracts minimally**

In `WorkflowItemPlan`, add:

```python
acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
depends_on: list[str] = Field(default_factory=list, max_length=50)
```

In `WorkflowPlan` validation, build the canonical-key map, validate every dependency target, reject self/duplicate dependencies, reject any target whose stage position is greater than or equal to the successor’s stage position when it is cross-stage, and perform Kahn/DFS cycle detection across all items. Keep error messages stable and prefixed with `workflow` semantics through `GroupWorkflowPlanError`.

**Step 4: Run the focused tests**

Run: `rtk pytest backend/tests/test_group_workflow_contracts.py -q`

Expected: PASS.

**Step 5: Run existing planning tests**

Run: `rtk pytest backend/tests/test_team_builder_planning.py -q`

Expected: initially identify fixtures/templates missing task acceptance criteria; update only their plan fixtures in later tasks, then rerun to PASS. Do not commit.

### Task 2: Migrate persistent task-DAG and task-change state

**Files:**
- Create: `backend/alembic/versions/202607311100_add_group_workflow_task_dag.py`
- Modify: `backend/app/models/group_workflow.py`
- Modify: `backend/alembic/env.py`
- Test: `backend/tests/test_group_workflow_service.py`

**Step 1: Write migration/model expectation tests**

Add unit assertions using SQLAlchemy metadata that `GroupWorkflowItem` exposes acceptance, task timestamps, failure fields and the new `GroupWorkflowTaskDependency` and `GroupWorkflowChangeRequest` models. Include migration-level assertions only if the repository’s Alembic tests already exercise migrations; otherwise verify metadata in the service tests.

**Step 2: Implement the model additions**

Add to `GroupWorkflowItem`:

```python
acceptance_criteria: Mapped[list]  # JSONB, non-null, default []
started_at: Mapped[datetime | None]
completed_at: Mapped[datetime | None]
failed_at: Mapped[datetime | None]
failure_code: Mapped[str | None]
failure_summary: Mapped[str | None]
```

Replace the item status check with exactly `pending`, `ready`, `in_progress`, `awaiting_approval`, `done`, `blocked`, and `failed`.

Add `GroupWorkflowTaskDependency` with workflow, predecessor, successor and creation time; enforce a unique predecessor/successor edge plus indexes for both traversal directions. Add `GroupWorkflowChangeRequest` with workflow, optional target item, requester, confirmer, kind, status (`pending`, `confirmed`, `rejected`), before/after/impact JSON, reason and timestamps. It is the durable record required for leader confirmation; it does not allow task deletion or skip.

Import the two new models from `backend/alembic/env.py` so metadata discovery is complete.

**Step 3: Implement the additive Alembic revision**

Use `scope_legacy_templates` as `down_revision`, because it is the current local head. The upgrade must:

1. add the item fields with safe defaults/backfill;
2. replace `ck_group_workflow_items_status` safely;
3. create dependency and change-request tables, FK constraints, unique constraints and indexes;
4. preserve existing workflows by leaving prior items `pending` until the compatibility reconciliation in Task 4 materializes stage-local readiness.

The downgrade must remove only the new tables/indexes/columns and restore the prior status constraint. Use inspector guards consistently with `202607301500_add_group_workflows.py` so a recreated Docker database and an upgraded database both work.

**Step 4: Verify migration shape**

Run: `rtk pytest backend/tests/test_group_workflow_service.py -q`

Expected: baseline tests may fail only on now-obsolete statuses; update them as part of Task 4.

Run: `rtk alembic -c backend/alembic.ini upgrade head`

Expected: exits 0 against the local configured database. Do not run destructive database commands.

### Task 3: Make every workflow producer emit a complete task DAG plan

**Files:**
- Modify: `backend/app/services/group_workflow/planning.py`
- Modify: `backend/app/services/group_workflow/templates.py`
- Modify: `backend/app/services/team_builder/planning.py`
- Test: `backend/tests/test_team_builder_planning.py`
- Test: `backend/tests/test_group_workflow_contracts.py`

**Step 1: Add failing producer tests**

Extend template and team-builder tests to assert every emitted task includes a non-empty acceptance contract and a valid dependency reference. For each default lifecycle, assert each first task in a later stage depends on the terminal required task(s) in the preceding stage.

**Step 2: Update the AI planning contract and prompt**

Change `_SYSTEM_PROMPT` in `planning.py` to require `items[].depends_on` and `items[].acceptance_criteria`, state the canonical reference format, permit safe same-stage parallelism, forbid forward-stage dependencies and explicitly prohibit conversational proof as evidence. Retain the strict JSON-only response and validate with `validate_workflow_plan` before storing the draft.

**Step 3: Update deterministic templates and team-builder conversion**

Give every template-generated and team-builder-generated task concrete task acceptance criteria. Preserve the existing source-code evidence marker and source/test requirement for technical roles. Generate dependencies between lifecycle stages, not a single leader-owned deliverable that can complete a stage by itself.

**Step 4: Verify all plan producers**

Run: `rtk pytest backend/tests/test_group_workflow_contracts.py backend/tests/test_team_builder_planning.py -q`

Expected: PASS.

### Task 4: Materialize DAG edges and introduce ready-only reconciliation

**Files:**
- Modify: `backend/app/services/group_workflow/service.py`
- Test: `backend/tests/test_group_workflow_service.py`

**Step 1: Write failing transition tests**

Add focused service tests with mocked async query responses or test database fixtures for current-stage root readiness, one-time release of all satisfied successors, dependency-blocked reasons after failures, stage incompleteness until every task is done, and rejection when a leader attempts to start or submit another member’s task.

Include the exact source-code/test-result evidence scenario already protected by `_validate_source_code_evidence`.

**Step 2: Add graph helpers**

Add three small, query-focused helpers in `service.py`: one to materialize plan dependencies from a workflow, plan and canonical item-key map; one to refresh successors using workflow, active stage and optional predecessor task ID; and one to return a concise dependency-block reason by task ID.

`create_workflow` must flush all items, resolve canonical plan dependencies to IDs, create dependency rows, and mark only roots in the active first stage `ready`. Later-stage roots remain `pending` until their stage activates. Existing workflow creation remains transactional; an error creates neither an incomplete graph nor dispatch events.

**Step 3: Replace status transition rules**

Require `ready` in `start_item`, set `started_at` on first transition to `in_progress`, and restrict start/evidence/block/unblock to the assigned agent—not the leader as a bypass. On valid evidence, append evidence, set `done` and `completed_at`, clear failure fields, then refresh only direct successors. A successor becomes `ready` iff its stage is active and every predecessor is `done`.

On `blocked` or `failed`, retain the task’s own reason/code and expose a derived dependency-blocked reason for downstream nodes; never mark downstream as done or dispatch them. Implement an explicit retry operation that moves only a failed/blocked task back to `ready` when its predecessors remain done, preserves history as events, and recomputes its descendants after eventual completion.

**Step 4: Derive stage advancement from task completion**

Update `_reconcile` and `_complete_stage` so a stage completes only after every task in it is `done` and its stage acceptance gate is satisfied. On activating the next stage, refresh that stage’s eligible roots and return the ready tasks for dispatch. Do not retain the old `member_progress` leader wake as a progression mechanism.

**Step 5: Run service tests**

Run: `rtk pytest backend/tests/test_group_workflow_service.py -q`

Expected: PASS.

### Task 5: Persist and enforce group-leader change confirmation

**Files:**
- Modify: `backend/app/services/group_workflow/service.py`
- Modify: `backend/app/models/group_workflow.py`
- Test: `backend/tests/test_group_workflow_service.py`

**Step 1: Write failing change-request tests**

Cover a leader requesting and confirming:

- adding an unstarted task;
- splitting an unstarted task into replacement work while retaining completion requirements;
- reconnecting dependencies with an impact preview;
- changing acceptance criteria with confirmation;
- rejection when requester/confirming actor is not the group leader;
- rejection when target tasks are in progress, awaiting approval, done, blocked, or failed;
- no deletion or skip endpoint/path.

**Step 2: Implement immutable requests and impact preview**

Add service methods such as `request_task_change`, `preview_task_change`, and `confirm_task_change`. The request stores before/after JSON and a calculated impact containing affected descendants, current ready tasks that would be invalidated, and stage completion implications. Only the group leader can confirm it. Confirmation locks workflow/tasks, re-runs complete DAG validation, applies the transaction, bumps the workflow version, writes an idempotent `GroupWorkflowEvent`, and enqueues only newly ready work.

**Step 3: Verify permission and audit behavior**

Run: `rtk pytest backend/tests/test_group_workflow_service.py -q`

Expected: PASS.

### Task 6: Dispatch task-ready events to assignees and reduce leader chatter

**Files:**
- Modify: `backend/app/services/group_workflow/service.py`
- Modify: `backend/app/services/group_workflow/worker.py`
- Modify: `backend/app/services/group_workflow/daily_digest.py`
- Modify: `backend/tests/test_group_workflow_leader_wake.py`

**Step 1: Write failing worker copy/dispatch tests**

Add tests that a `task_ready` event is rendered for and queued to the assignee with task name, acceptance criteria and predecessor-output summary. Assert routine task completion does not enqueue a generic leader “member progress” prompt. Assert leader events are limited to blocker/failed, change confirmation, acceptance exception, stage activation/completion and compact progress summaries.

**Step 2: Implement task dispatch events**

Create `task_ready` events using an idempotency key that includes task ID and task version. Extend worker claim/dispatch logic to resolve the task assignee and enqueue the activation message to that agent’s group session. Include the task ID in the structured payload and all public system messages.

**Step 3: Make leader notifications exception-oriented**

Replace generic `member_progress` content in `build_leader_wake_content` and `_leader_workflow_instruction` with task-DAG instructions: examine blocker chain, confirm a pending change, or review a concrete acceptance exception. Leave daily digest as read-only reporting if retained, and ensure it never changes task/stage state or triggers duplicate task assignment.

**Step 4: Run worker tests**

Run: `rtk pytest backend/tests/test_group_workflow_leader_wake.py -q`

Expected: PASS.

### Task 7: Scope agent context and runtime tools to assigned ready tasks

**Files:**
- Modify: `backend/app/services/agent_runtime/group_context_builder.py`
- Modify: `backend/app/services/agent_runtime/group_runtime_tools.py`
- Test: `backend/tests/test_group_workflow_leader_wake.py`
- Create: `backend/tests/test_group_workflow_runtime_tools.py`

**Step 1: Add failing runtime authorization tests**

Test that an agent sees only its `ready`, `in_progress`, `blocked`, and `failed` assignments (not another member’s pending work); cannot start pending/dependency-blocked work; cannot submit evidence for another member; and receives predecessor evidence and acceptance criteria for a ready task.

**Step 2: Update runtime context**

Replace the current pending-inclusive query in `group_context_builder.py` with an assignee-scoped DAG task snapshot. For each task include task key, state, acceptance criteria, predecessor titles/statuses/evidence summary, and the exact allowed next action. Do not emit internal decision-maker/tool instructions into public group content.

**Step 3: Update runtime tools**

Keep existing workflow tool names where possible for compatibility, but make `start` accept `ready` only and make evidence/blocked/retry actions call the new service methods. Add a task-detail/read tool only if existing tool payload limits prevent including predecessors in context. Return user-safe `code`, `message`, task status and graph-impact fields.

**Step 4: Run focused runtime tests**

Run: `rtk pytest backend/tests/test_group_workflow_runtime_tools.py backend/tests/test_group_workflow_leader_wake.py -q`

Expected: PASS.

### Task 8: Expose DAG, task operations, and leader-confirmed changes over HTTP

**Files:**
- Modify: `backend/app/api/group_workflows.py`
- Test: `backend/tests/test_group_workflow_api.py`

**Step 1: Write failing API tests**

Create API tests for workflow graph serialization, ready-task lifecycle, retry, change request, change preview, confirm/reject, 403 leader permission and 409 optimistic conflict. Assert responses never report a task `done` before accepted evidence exists.

**Step 2: Expand snapshot serialization**

Return item acceptance criteria, lifecycle/failure timestamps, `depends_on`, `blocked_by`, ready/blocked counters and pending leader change requests. Keep existing fields for frontend compatibility.

**Step 3: Add endpoint models and routes**

Use existing `/api/groups/{group_id}/workflow` routing conventions. Add typed request bodies and routes for item retry, task details if needed, task change request/preview/confirm/reject. Reuse `_scope`; confirmation requires the group leader, while normal task operations require the assigned participant. Convert DAG validation errors to deterministic 400/409 responses via `_workflow_error`, then publish realtime workflow change only after a successful transaction.

**Step 4: Run API tests**

Run: `rtk pytest backend/tests/test_group_workflow_api.py -q`

Expected: PASS.

### Task 9: Type the expanded workflow graph in the frontend

**Files:**
- Modify: `frontend/src/types/groupWorkflow.ts`
- Modify: `frontend/src/pages/groups/GroupWorkflowTab.tsx`
- Test: `frontend/tests/group-workflow-types.test.mjs`

**Step 1: Add a failing serialization/type fixture test**

Add a node test fixture for an active workflow with parallel ready tasks, a dependency-blocked successor, a failure record, and a pending leader change request. The test must validate the component’s exported normalization helpers if present; otherwise make the TypeScript build the primary verification in Step 4.

**Step 2: Expand TypeScript domain types**

Add `ready`, `failed` item states, task dependency/blocked-by fields, task acceptance/timestamps/failure fields, graph summary, and typed change-request payloads. Avoid `any`; use `Record<string, unknown>` only for unstructured evidence and event payload compatibility.

**Step 3: Update fetch and mutation helpers**

Keep all workflow fetches and mutations in `GroupWorkflowTab.tsx` (or extract a small local API module only if it removes duplication). Wire optimistic-version handling to reload the graph on 409 and show the server’s user-safe message.

**Step 4: Verify frontend type safety**

Run: `rtk npm --prefix frontend run build`

Expected: PASS.

### Task 10: Build the DAG command center and task-details side panel

**Files:**
- Create: `frontend/src/pages/groups/WorkflowDagView.tsx`
- Create: `frontend/src/pages/groups/WorkflowTaskDrawer.tsx`
- Modify: `frontend/src/pages/groups/GroupWorkflowTab.tsx`
- Modify: `frontend/src/pages/groups/groups.css`
- Test: `frontend/tests/group-workflow-dag.test.mjs`

**Step 1: Add failing UI behavior tests**

Cover pure helpers/components for grouping nodes by stage, deriving ready queue and blocker chain, status label/class mapping, and leader-only change controls. Use a fixture where integration cannot render until three predecessors are done.

**Step 2: Implement a dependency-aware DAG view without a new graph dependency**

Render columns by ordered stage and task cards by status; draw SVG connector paths from `depends_on` data behind cards. Cards must be keyboard accessible and open a side drawer. State colors distinguish `ready`, `in_progress`, `blocked`, `failed`, `awaiting_approval`, and `done`; never use a visual completed state for failed/blocked work.

**Step 3: Implement task drawer and operational queues**

The drawer shows task target, assigned member, predecessor/successor links, delivery evidence, task-level acceptance criteria, execution messages/events, failure summary, and any related AI monitoring IDs already returned by the backend. The main panel shows stage rail, ready queue, blocker chain, and leader pending-change cards. Normal users see execution state; only the group leader sees request/confirm controls.

**Step 4: Update CSS responsively**

Extend the existing workflow styles rather than restyling unrelated group UI. On narrow screens, preserve readable stage columns with horizontal scrolling and make the detail drawer full-width. Provide focus outlines and text labels in addition to color.

**Step 5: Verify UI behavior and build**

Run: `rtk npm --prefix frontend test`

Expected: PASS.

Run: `rtk npm --prefix frontend run build`

Expected: PASS.

### Task 11: Verify end-to-end invariants and migration safety

**Files:**
- Modify: `backend/tests/test_group_workflow_service.py`
- Modify: `backend/tests/test_group_workflow_leader_wake.py`
- Modify: `backend/tests/test_team_builder_planning.py`
- Modify: `frontend/tests/group-workflow-dag.test.mjs`

**Step 1: Add the acceptance scenario**

Create one lifecycle fixture spanning requirements, parallel implementation, integration and release. Assert:

1. only initial roots dispatch;
2. implementation tasks release in parallel only after requirements;
3. integration cannot become ready while any required implementation/test task is incomplete, failed or lacks source/test evidence;
4. the stage cannot advance via chat, leader action, document-only evidence or a time-based digest;
5. group-leader-confirmed split/relink operations update the graph atomically;
6. duplicate event processing does not create duplicate dispatches.

**Step 2: Run all workflow-focused backend tests**

Run: `rtk pytest backend/tests/test_group_workflow_contracts.py backend/tests/test_group_workflow_service.py backend/tests/test_group_workflow_leader_wake.py backend/tests/test_group_workflow_runtime_tools.py backend/tests/test_group_workflow_api.py backend/tests/test_team_builder_planning.py -q`

Expected: PASS.

**Step 3: Run static and frontend checks**

Run: `rtk npm --prefix frontend test && rtk npm --prefix frontend run build`

Expected: PASS.

Run: `rtk git diff --check`

Expected: no whitespace errors.

**Step 4: Report current-branch changes without committing**

Run: `rtk git status --short`

Expected: report only; do not stage, commit, push, reset, checkout, stash, or overwrite pre-existing user changes.
