# Intelligent Team Builder Implementation Plan

> Implements the approved design in `docs/superpowers/specs/2026-07-30-intelligent-team-builder-design.md`.

## Delivery order

1. Add the durable schema and group-leader contract.
2. Add the team-builder planner, draft APIs, and provisioning worker.
3. Add the Groups-page wizard and job-progress experience.
4. Add full backend and frontend contract coverage, then run the focused suites and production build.

## 1. Schema and group-leader contract

**Files**

- Create `backend/app/models/team_builder.py`.
- Modify `backend/app/models/group.py`.
- Modify `backend/alembic/env.py` and `backend/app/main.py` model-registration imports.
- Create `backend/alembic/versions/202607301200_add_team_builder_and_group_leader.py`.
- Extend `backend/tests/test_unified_runtime_group_migration.py`.

**Work**

1. Define `TeamBuildDraft`, `TeamProvisionJob`, and `TeamProvisionMember` SQLAlchemy models. Every row carries `tenant_id`; use UUID primary keys, UTC timestamps, status strings guarded by check constraints, JSONB for raw constraints and validated plans, and foreign keys to the requesting user, draft, agents, participants, and final group where applicable.
2. Add a unique job idempotency constraint scoped to the draft, and an index for workers to claim jobs by `(status, updated_at)`.
3. Add nullable `Group.leader_participant_id`, referencing `participants.id`. Keep it nullable at the database level for existing groups, but require it for every new group in domain services.
4. Backfill neither agents nor leaders. Legacy groups remain visible with no leader and return an explicit `leader_not_assigned` state.
5. Register the new models in Alembic and the application startup import list so metadata-based test databases include them.

**Acceptance**

- Upgrade and downgrade migrate cleanly on PostgreSQL and SQLite test paths.
- A legacy group can still load, while a newly created team-builder group requires one agent participant as leader.

## 2. Group APIs and domain service updates

**Files**

- Modify `backend/app/api/groups.py`.
- Modify `backend/app/services/group_chat_service.py`.
- Modify `backend/app/models/group.py` and `frontend/src/types/group.ts` response contracts.
- Modify `frontend/src/services/groupApi.ts`.
- Extend `backend/tests/test_group_api.py` and `backend/tests/test_group_chat_service.py`.

**Work**

1. Expose the leader in `GroupOut` and the frontend `Group` type, including a compact leader display payload or enough participant identity to resolve it without an N+1 request.
2. Extend the service-level group creation path with `leader_participant_id`; validate tenant, visibility, active agent status, and membership before flushing. Stage the creator, all members, and leader reference in one transaction.
3. Update the existing manual create-group API and modal to require choosing an eligible agent leader. If the user has no suitable agent, provide a link into the intelligent builder instead of silently creating a generic agent from the modal.
4. Add a lightweight leader-resolution endpoint only if the existing member payload cannot render the header and composer behavior without extra requests.
5. Preserve the human `manager` role and all existing membership authorization behavior. Do not grant the leader human management capabilities.

**Acceptance**

- Invalid leader IDs, users, inactive agents, or agents outside the tenant fail before any group rows are staged.
- Existing group list, membership, session, and chat contracts remain compatible.

## 3. Team-plan contract and generator

**Files**

- Create `backend/app/services/team_builder/planning.py`.
- Create `backend/app/services/team_builder/service.py` and `backend/app/services/team_builder/errors.py`.
- Create `backend/app/api/team_builder.py`.
- Modify `backend/app/main.py` to include the router.
- Create `backend/tests/test_team_builder_planning.py` and `backend/tests/test_team_builder_api.py`.

**Work**

1. Define a strict Pydantic/domain contract for a team plan: group name, goal, assumptions, work phases, one leader spec, member specs, template/model/skill choices, source (`existing` or `new`), and explicit delegation edges.
2. Use the tenant's planning-capable model resolution, but implement a separate prompt and output validator. Do not call `group_message_service` or reuse the group-planning JSON schema, because no group exists at draft time.
3. Build draft APIs:
   - `POST /api/team-build-drafts` persists the requirement, creates `generating`, and launches generation.
   - `GET /api/team-build-drafts/{id}` returns ownership-scoped draft state and plan.
   - `PATCH /api/team-build-drafts/{id}` validates allowed edits, increments `plan_version`, and clears prior confirmation.
   - `POST /api/team-build-drafts/{id}/confirm` checks the supplied plan version, records one idempotency key, and creates or returns one provision job.
   - `GET /api/team-provision-jobs/{id}` returns durable progress and safe diagnostics.
4. Validate quota, tenant model availability, agent access, and template availability before confirmation. Surface structured error codes appropriate for retry or user correction.

**Acceptance**

- Generation creates no `Agent`, `Group`, `GroupMember`, or `ChatSession` records.
- Invalid model output or unavailable models leave a retriable or editable draft, never a partial team.
- Editing after review invalidates stale confirmation attempts.

## 4. Durable provisioning worker and activation

**Files**

- Create `backend/app/services/team_builder/provisioning.py`.
- Create `backend/app/services/team_builder/worker.py`.
- Modify `backend/app/main.py` lifespan worker startup and shutdown.
- Modify `backend/app/services/group_file_service.py` only if a narrow service-level helper is needed for initial workspace documents.
- Create `backend/tests/test_team_builder_provisioning.py`.

**Work**

1. Implement a DB-claimed, `SKIP LOCKED` worker loop, enabled for `PROCESS_ROLE=worker`, following the existing scheduler/Runtime daemon lifecycle pattern. Do not use request-scoped `BackgroundTasks` for the provision job.
2. Claim one job transactionally, move it through the approved states, and persist each resolved/reused/created agent before proceeding. New agents are created through a shared internal creation service extracted from `api/agents.py`; do not call the HTTP endpoint internally.
3. Poll newly created agents until their normal initialization reaches `idle` or terminal `error`. On error, set the job to `retryable_failed` with per-member diagnostics.
4. Once all members are ready, create the group, all memberships, the primary session, and `leader_participant_id` in one transaction through `group_chat_service`.
5. Write `TEAM_BRIEF.md` and `TEAM_ROSTER.md` through the group file service, using the confirmed plan and leader operating instructions.
6. Create the kickoff through `group_message_service.enqueue_group_message`, with the requesting user's participant as sender and the leader as the sole mention. Store the message ID before marking the job complete. This makes the initial leader run use the normal public group dispatch route.
7. Make every stage replay-safe: reuse member records, group ID, session ID, and activation message ID on retries. Never delete automatically created agents in the worker.

**Acceptance**

- Restart, duplicate confirmation, and repeated worker claims produce one group and one kickoff message.
- A group is never created until every planned member is ready.
- The leader receives the first user-authored instruction and can use the existing public `at` handoff to wake contributors.

## 5. Leader operating instructions and group interaction

**Files**

- Create a leader template or provisioned leader prompt asset under `backend/agent_templates/`.
- Modify the internal agent-provisioning service from step 4 to apply that template/prompt.
- Modify `frontend/src/pages/groups/GroupsPage.tsx`.
- Modify `frontend/src/pages/groups/MessageComposer.tsx`.
- Modify `frontend/src/pages/groups/GroupSidePanel.tsx` or header components as appropriate.
- Extend `backend/tests/test_agent_runtime_group_handoff.py` and `frontend/tests/groupInteractionContract.test.mjs`.

**Work**

1. Define the leader's operating contract: treat messages from the user as team goals, make an explicit public plan, mention contributors only as needed, preserve public evidence, track blockers, and provide a final summary.
2. Render the leader's name and role in the group header and side panel.
3. When a leader exists, initialize the composer mention binding with that participant while retaining the user's ability to remove it or deliberately mention additional agents. Preserve IME behavior and existing message-id idempotency.
4. Keep contributor messages fully visible. The feature changes the default routing target, not message visibility or runtime delivery semantics.

**Acceptance**

- A user can send a natural message without selecting an agent and it reaches the leader.
- Contributors' public messages and existing typing/run indicators continue to render.

## 6. Team-builder frontend

**Files**

- Create `frontend/src/pages/groups/TeamBuilderModal.tsx` or focused child components under `frontend/src/pages/groups/team-builder/`.
- Modify `frontend/src/pages/groups/GroupsPage.tsx`.
- Modify `frontend/src/services/groupApi.ts` or create `frontend/src/services/teamBuilderApi.ts`.
- Create `frontend/src/types/teamBuilder.ts`.
- Modify `frontend/src/pages/groups/groups.css`.
- Modify `frontend/src/i18n/en.json` and `frontend/src/i18n/zh.json`.
- Create `frontend/tests/teamBuilderContract.test.mjs`.

**Work**

1. Add a distinct intelligent-builder entry beside the existing plus button. Keep the manual group modal reachable.
2. Build three views in one recoverable modal/drawer: requirement form, editable plan review, and provisioning progress. Persist draft ID and job ID in route/query state or local storage so a refresh restores the correct stage.
3. The review view clearly labels reused versus newly created agents, the leader, role purpose, generated assumptions, and warnings. Confirmation sends the draft ID, current plan version, and a client-generated idempotency UUID.
4. Poll job state while the progress view is open. On completion, invalidate group queries and navigate to `/groups/{groupId}/{sessionId}`. On retryable failure, offer retry only when the backend reports it is safe.
5. Add localized Chinese and English copy for all new states and errors.

**Acceptance**

- No resource is created before the review confirmation action.
- Browser refresh during generation or provisioning resumes the correct state.
- Completion routes the user directly into the newly activated group.

## 7. Verification and release checks

**Files**

- Add or update the tests named in steps 1 through 6.

**Commands**

```bash
cd backend && pytest tests/test_team_builder_planning.py tests/test_team_builder_api.py tests/test_team_builder_provisioning.py tests/test_group_api.py tests/test_group_chat_service.py tests/test_agent_runtime_group_handoff.py -q
cd backend && ruff check app tests && ruff format --check app tests
cd frontend && node --test tests/teamBuilderContract.test.mjs tests/groupApiContract.test.mjs tests/groupInteractionContract.test.mjs
cd frontend && npm run build
```

Run the complete backend suite before merge if time allows. Manually verify one successful build, an unavailable-model failure, an agent-initialization failure followed by retry, duplicate confirmation, a browser refresh while provisioning, and a group conversation that starts with the leader and visibly delegates to contributors.
