# Intelligent Team Builder Design

## Status

Approved approach: persistent team-building draft plus asynchronous provisioning.

## Goal

Let a user describe a business need, review an AI-generated team design, and then create and activate a group where one dedicated agent acts as the group leader. The user communicates only with the leader; all member execution messages remain visible in the group.

## Scope

The feature covers the flow below.

```text
Requirement -> team design -> user confirmation -> provision team -> create group -> activate leader
```

It creates missing agents, reuses suitable existing agents, assigns exactly one agent leader to every new group, and starts the group with a message authored by the requesting user.

The existing manual group-creation flow remains available for ad-hoc groups.

## User Experience

### Entry

Add an `Intelligent team builder` action beside the existing `Create group` action on `/groups`. The manual creation modal remains unchanged in purpose, but new manually created groups must select an existing leader or create one through the same leader-provisioning path.

### Draft flow

1. The user supplies a requirement, an optional team name, and constraints such as deadline, preferred roles, budget, available tools, and existing agents to include or avoid.
2. The system generates a persisted, versioned draft. No group or agent is created at this point.
3. The user reviews and can edit the group name, plan, team roster, leader, and individual role specifications.
4. The user confirms one exact draft version.
5. The UI shows a durable provisioning screen until the team is ready, then navigates to the created group.

The draft displays the following facts in structured form:

- goal, assumptions, constraints, phases, and initial work items;
- the designated group leader;
- each member's role, responsibility, source (`existing` or `new`), selected template, skills, model, and reason for inclusion;
- expected delegation paths from the leader to contributors;
- count of agents to create and validation warnings.

### Group interaction

The group shows every public message from the leader and contributors. The composer defaults to mentioning the group leader, so the user can write natural instructions without manually choosing a contributor. The leader's system instruction requires it to decompose work, visibly mention the necessary group members, track dependencies, and post a concise final summary to the user.

## Domain Model

Introduce a dedicated `team_builder` domain rather than embedding planning and provisioning behavior in group routes.

### TeamBuildDraft

- identity, tenant, creator, timestamps, expiry, and status;
- immutable raw requirement and constraints;
- `plan_version` and the validated generated plan JSON;
- editable reviewed plan JSON;
- confirmation timestamp and the confirmed plan version.

Draft states: `generating`, `ready`, `invalid`, `confirmed`, `expired`, `cancelled`.

### TeamProvisionJob

- draft, tenant, requesting user, idempotency key, status, timestamps, and error details;
- per-member provision records, including reuse/create decision, agent ID, participant ID, and readiness state;
- created group ID, leader participant ID, initial session ID, and activation message ID.

Job states: `queued`, `validating`, `provisioning_agents`, `waiting_for_agents`, `creating_group`, `activating`, `completed`, `retryable_failed`, `failed`.

### Group leader

Add `groups.leader_participant_id`, referencing the leader's participant. Domain validation must ensure that it belongs to the group, is an agent participant, and is active. It is distinct from `group_members.role`:

- the human requester remains the group `manager` and retains authorization controls;
- the leader is the business orchestration agent, not a human permission role;
- every newly created group has exactly one leader.

Existing groups migrate into a `leader not assigned` state and are prompted to select or create a leader before their next management or task-start action. The migration must not silently create agents for historical groups.

## APIs

- `POST /api/team-build-drafts`: validate requirement and create or queue draft generation.
- `GET /api/team-build-drafts/{id}`: return a draft and its validated plan.
- `PATCH /api/team-build-drafts/{id}`: save edits and increment the plan version.
- `POST /api/team-build-drafts/{id}/confirm`: atomically bind an idempotency key to a confirmed plan version and enqueue one provision job.
- `GET /api/team-provision-jobs/{id}`: return job state, member progress, and actionable failure diagnostics.

Use an internal provisioning service to create the final group rather than composing public group APIs from the browser. It must reuse the existing agent creation, participant, group membership, session, message-intake, and runtime facilities.

## Planning and Provisioning

The team-design generator uses the tenant's configured planning-capable model but a new prompt and a new strict JSON contract. It must not reuse the existing group task-planning contract: existing planning runs divide work among members after a group and its members already exist.

On confirmation, the worker performs these steps:

1. Revalidate tenant access, model availability, quota, and the confirmed plan version.
2. Resolve eligible existing agents and create only missing agents, tagged to the provision job.
3. Wait until every required agent is available. Reuse the project's normal asynchronous agent initialization rather than holding an HTTP request open.
4. In one database transaction, create the group, requester membership, all agent memberships, initial session, and leader reference.
5. Persist `TEAM_BRIEF.md` and `TEAM_ROSTER.md` in the group workspace.
6. Create exactly one first group message using the requesting user's participant, mention the leader, and instruct it to begin from the approved brief.
7. Mark the job complete only after the activation message is durable and the corresponding leader run has been accepted.

The leader then uses the existing public group `at` handoff to distribute work. Contributors keep posting public messages; the leader synthesizes their output.

## Reliability and Safety

- Generation and confirmation use explicit status transitions and plan versions; a stale draft cannot be confirmed.
- Confirmation requires an idempotency key. Refreshes and double-clicks return the same job.
- Provisioned agents are recorded before later steps, so retries reuse them rather than creating duplicates.
- A partially failed job never creates a half-populated group. It remains retryable with clear member-level errors.
- The system does not auto-delete provisioned agents after failure; the user can retry safely and an administrator can clean up explicitly.
- Activation is idempotent through the stored activation message ID. It cannot emit duplicate kickoff instructions.
- Quota, permissions, agent visibility, model availability, and runtime failure errors are surfaced before or during provisioning with a retry path where safe.

## Frontend Components

- `TeamBuilderEntry`: entry action on the Groups page.
- `TeamRequirementForm`: requirement and constraints.
- `TeamPlanReview`: editable roster, leader, plan, validation warnings, and confirmation.
- `TeamProvisionProgress`: durable progress with retry and navigation on completion.
- group composer update: default leader mention for groups that have a leader.
- group header update: display the leader identity and provide a direct leader focus action.

All new copy is added to the existing i18n catalogues.

## Testing

- Unit tests for plan-schema validation, leader eligibility, version checks, idempotency, and state transitions.
- Service tests for reuse versus creation, agent readiness waiting, transactional group creation, duplicate-confirm handling, and activation-message deduplication.
- API tests for authorization, quota failures, unavailable models, and retryable provisioning failures.
- Frontend tests for draft review edits, progress recovery after refresh, leader-default mentions, and displaying contributor messages.
- End-to-end flow: requirement -> review -> confirmation -> leader and members become ready -> group opens -> user kickoff reaches leader -> leader publicly delegates.

## Out of Scope

- Hiding contributor messages from the user.
- Replacing the existing group task-planning runtime.
- Automatically creating a leader for legacy groups without an explicit human decision.
- Automatic deletion of agents created by a failed provisioning job.
