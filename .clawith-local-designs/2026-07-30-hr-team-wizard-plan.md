# HR Team Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the HR-group chat entry with a Projects page wizard: fill requirements → generate proposals → create team (no auto-kickoff) → editable「生成并发送」@群主 kickoff as the current user.

**Architecture:** Reuse `POST /projects/team-plans` and `POST /hr-review/sessions/{id}/select` for plan generation and provision. Add `send_kickoff=False` to provisioning. Add `kickoff/draft` + `kickoff/send` on `/api/projects`. Rebuild `Projects.tsx` as a four-step wizard; remove「去 HR 群提需求」CTA.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, pytest, React 19 + TypeScript, existing `HrProposalCard` / `projectApi` / `hrReviewApi`.

**Spec:** `.clawith-local-designs/2026-07-30-hr-team-wizard-design.md`

## Global Constraints

- Wizard path must never auto-send kickoff on create.
- Kickoff send must use the **current user's** participant identity and mention the group leader.
- Duplicate kickoff returns HTTP 200 with `already_sent: true` (no second message).
- Remove Projects main CTA to HR review board (do not restore as secondary).
- Shareholder group entry may remain.
- Prefer Chinese UI copy consistent with existing Projects page.
- Python imports at file top unless circular-import requires otherwise.
- Plan location is `.clawith-local-designs/` because `docs/` is gitignored in this repo.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/alembic/versions/202607301200_add_project_kickoff_sent_at.py` | Add `kickoff_sent_at` column |
| `backend/app/models/project.py` | `ProjectWorkflow.kickoff_sent_at` |
| `backend/app/services/project_provisioning.py` | `send_kickoff` flag |
| `backend/app/services/hr_review_session_service.py` | Pass `send_kickoff` into provision |
| `backend/app/api/hr_review.py` | Optional body `send_kickoff` |
| `backend/app/services/project_kickoff_service.py` | draft + send kickoff logic |
| `backend/app/api/projects.py` | kickoff routes + `ProjectOut.kickoff_sent_at` |
| `backend/tests/test_project_kickoff.py` | Unit/API-oriented tests for flag + draft/send |
| `frontend/src/types/project.ts` | Types for kickoff + `kickoff_sent_at` + proposal card fields |
| `frontend/src/services/projectApi.ts` | `kickoffDraft` / `kickoffSend` |
| `frontend/src/services/hrReviewApi.ts` | `selectProposal(..., { send_kickoff: false })` |
| `frontend/src/pages/Projects.tsx` | Four-step wizard UI |

---

### Task 1: Add `kickoff_sent_at` to ProjectWorkflow

**Files:**
- Create: `backend/alembic/versions/202607301200_add_project_kickoff_sent_at.py`
- Modify: `backend/app/models/project.py`
- Modify: `backend/app/api/projects.py` (`ProjectOut`, `_project_out`)
- Modify: `frontend/src/types/project.ts`
- Test: `backend/tests/test_project_kickoff.py` (model field presence via import smoke; full API tests in later tasks)

**Interfaces:**
- Produces: `ProjectWorkflow.kickoff_sent_at: datetime | None`
- Produces: `ProjectOut.kickoff_sent_at: datetime | None`

- [ ] **Step 1: Write migration**

Create `backend/alembic/versions/202607301200_add_project_kickoff_sent_at.py`:

```python
"""Add kickoff_sent_at to project_workflows.

Revision ID: add_project_kickoff_sent_at
Revises: add_board_escalations
Create Date: 2026-07-30 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_project_kickoff_sent_at"
down_revision: str | None = "add_board_escalations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "project_workflows" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "kickoff_sent_at" not in _columns("project_workflows"):
        op.add_column(
            "project_workflows",
            sa.Column("kickoff_sent_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if "project_workflows" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "kickoff_sent_at" in _columns("project_workflows"):
        op.drop_column("project_workflows", "kickoff_sent_at")
```

If `alembic heads` shows a different head than `add_board_escalations`, set `down_revision` to the actual head before committing.

- [ ] **Step 2: Update model**

In `backend/app/models/project.py`, add after `updated_at`:

```python
    kickoff_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: Expose on ProjectOut**

In `backend/app/api/projects.py`:

```python
class ProjectOut(BaseModel):
    ...
    kickoff_sent_at: datetime | None = None
```

In `_project_out`, pass `kickoff_sent_at=workflow.kickoff_sent_at`.

- [ ] **Step 4: Update frontend type**

In `frontend/src/types/project.ts` on `ProjectWorkflow`:

```typescript
    kickoff_sent_at: string | null;
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/202607301200_add_project_kickoff_sent_at.py \
  backend/app/models/project.py backend/app/api/projects.py frontend/src/types/project.ts
git commit -m "$(cat <<'EOF'
feat: add project workflow kickoff_sent_at

Track whether the user has sent the execution-group start message so create and start can be split.
EOF
)"
```

---

### Task 2: `provision_team_from_plan(send_kickoff=...)`

**Files:**
- Modify: `backend/app/services/project_provisioning.py`
- Create: `backend/tests/test_project_kickoff.py`
- Modify: `backend/app/services/hr_review_session_service.py` (`select_proposal`)
- Modify: `backend/app/api/hr_review.py` (`SelectProposalIn`)

**Interfaces:**
- Consumes: existing `provision_team_from_plan`
- Produces:
  ```python
  async def provision_team_from_plan(..., send_kickoff: bool = True) -> dict
  async def select_proposal(..., send_kickoff: bool = True) -> dict
  class SelectProposalIn: proposal_id: str; proposals: list | None = None; send_kickoff: bool = True
  ```

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_project_kickoff.py`:

```python
from __future__ import annotations

import inspect

from app.services.project_provisioning import provision_team_from_plan
from app.services.hr_review_session_service import select_proposal


def test_provision_team_from_plan_accepts_send_kickoff_kwarg():
    params = inspect.signature(provision_team_from_plan).parameters
    assert "send_kickoff" in params
    assert params["send_kickoff"].default is True


def test_select_proposal_accepts_send_kickoff_kwarg():
    params = inspect.signature(select_proposal).parameters
    assert "send_kickoff" in params
    assert params["send_kickoff"].default is True
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd backend && pytest tests/test_project_kickoff.py::test_provision_team_from_plan_accepts_send_kickoff_kwarg tests/test_project_kickoff.py::test_select_proposal_accepts_send_kickoff_kwarg -v
```

Expected: FAIL (missing parameter).

- [ ] **Step 3: Implement `send_kickoff` in provisioning**

Change signature:

```python
async def provision_team_from_plan(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    creator_display_name: str,
    creator_avatar_url: str | None,
    project_name: str,
    requirements: str,
    roles: list[dict],
    template_key: str = "hr_generated",
    send_kickoff: bool = True,
) -> dict:
```

Keep building `wake_up_message` always. Wrap the `enqueue_group_message` kickoff block:

```python
    if send_kickoff:
        try:
            await group_message_service.enqueue_group_message(
                db,
                tenant_id=tenant_id,
                group_id=group.id,
                session_id=session.id,
                sender_participant_id=human_participant.id,
                content=wake_up_message,
                mention_participant_ids=[leader_participant.id],
                message_id=uuid.uuid4(),
            )
        except GroupMessageServiceError as exc:
            raise ProjectProvisioningError(f"Project kickoff could not be created: {exc}") from exc
```

When `send_kickoff` is True and message succeeds, also set `workflow.kickoff_sent_at = datetime.now(UTC)` so legacy auto-kickoff stays consistent with the new field. When False, leave `kickoff_sent_at` as None.

- [ ] **Step 4: Thread through `select_proposal` and API**

In `select_proposal`:

```python
async def select_proposal(
    db: AsyncSession,
    *,
    hr_session_id: uuid.UUID,
    proposal_id: str,
    user: User,
    fallback_proposals: list | None = None,
    send_kickoff: bool = True,
) -> dict:
```

Pass `send_kickoff=send_kickoff` into `provision_team_from_plan(...)`.

In `backend/app/api/hr_review.py` `SelectProposalIn`, add:

```python
send_kickoff: bool = True
```

And pass `send_kickoff=body.send_kickoff` into `select_proposal`.

Leave `create_project` / other callers on default `True`.

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd backend && pytest tests/test_project_kickoff.py::test_provision_team_from_plan_accepts_send_kickoff_kwarg tests/test_project_kickoff.py::test_select_proposal_accepts_send_kickoff_kwarg -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/project_provisioning.py \
  backend/app/services/hr_review_session_service.py \
  backend/app/api/hr_review.py \
  backend/tests/test_project_kickoff.py
git commit -m "$(cat <<'EOF'
feat: allow provisioning without auto kickoff

Split team creation from start-message send so the wizard can edit and send @leader kickoff separately.
EOF
)"
```

---

### Task 3: Kickoff draft + send service and API

**Files:**
- Create: `backend/app/services/project_kickoff_service.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/tests/test_project_kickoff.py`
- Modify: `frontend/src/services/projectApi.ts`
- Modify: `frontend/src/types/project.ts`

**Interfaces:**
- Produces:
  ```python
  class ProjectKickoffError(RuntimeError): ...

  async def draft_kickoff_message(db, *, workflow_id, tenant_id, user_id, instructions: str | None = None) -> dict
  # returns {content, leader_participant_id, leader_name, group_id, session_id}

  async def send_kickoff_message(db, *, workflow_id, tenant_id, user: User, content: str) -> dict
  # returns {group_id, session_id, message_id, already_sent}
  ```
- API:
  - `POST /api/projects/{workflow_id}/kickoff/draft`
  - `POST /api/projects/{workflow_id}/kickoff/send`

- [ ] **Step 1: Extend failing tests**

Append to `backend/tests/test_project_kickoff.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import project_kickoff_service


@pytest.mark.asyncio
async def test_draft_kickoff_falls_back_to_template_when_llm_fails():
    workflow_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    leader_participant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()

    workflow = SimpleNamespace(
        id=workflow_id,
        tenant_id=tenant_id,
        name="跨境店",
        requirements="做 Shopify 一件代发",
        status="active",
        team_plan={
            "project_name": "跨境店",
            "requirements": "做 Shopify 一件代发",
            "roles": [
                {
                    "key": "leader",
                    "name": "运营负责人",
                    "duties": "统筹",
                    "soul": "# L",
                    "is_group_leader": True,
                    "suggested_tools": [],
                },
                {
                    "key": "ops",
                    "name": "选品专员",
                    "duties": "选品",
                    "soul": "# O",
                    "is_group_leader": False,
                    "suggested_tools": [],
                },
            ],
        },
        group_id=group_id,
        group_leader_agent_id=uuid.uuid4(),
        kickoff_sent_at=None,
    )

    with (
        patch.object(project_kickoff_service, "_load_workflow", AsyncMock(return_value=workflow)),
        patch.object(
            project_kickoff_service,
            "_resolve_execution_context",
            AsyncMock(
                return_value={
                    "group_id": group_id,
                    "session_id": session_id,
                    "leader_participant_id": leader_participant_id,
                    "leader_name": "运营负责人",
                }
            ),
        ),
        patch.object(
            project_kickoff_service,
            "_llm_draft_kickoff",
            AsyncMock(side_effect=RuntimeError("llm down")),
        ),
    ):
        result = await project_kickoff_service.draft_kickoff_message(
            AsyncMock(),
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
        )

    assert result["leader_name"] == "运营负责人"
    assert "请现在启动团队" in result["content"]
    assert str(result["group_id"]) == str(group_id)


@pytest.mark.asyncio
async def test_send_kickoff_is_idempotent_when_already_sent():
    workflow_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    from datetime import UTC, datetime

    workflow = SimpleNamespace(
        id=workflow_id,
        tenant_id=tenant_id,
        status="active",
        group_id=uuid.uuid4(),
        kickoff_sent_at=datetime.now(UTC),
    )
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, display_name="Me", avatar_url=None)

    with (
        patch.object(project_kickoff_service, "_load_workflow", AsyncMock(return_value=workflow)),
        patch.object(
            project_kickoff_service,
            "_resolve_execution_context",
            AsyncMock(
                return_value={
                    "group_id": workflow.group_id,
                    "session_id": uuid.uuid4(),
                    "leader_participant_id": uuid.uuid4(),
                    "leader_name": "运营负责人",
                }
            ),
        ),
    ):
        result = await project_kickoff_service.send_kickoff_message(
            AsyncMock(),
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            user=user,
            content="@运营负责人 开工",
        )

    assert result["already_sent"] is True
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

```bash
cd backend && pytest tests/test_project_kickoff.py::test_draft_kickoff_falls_back_to_template_when_llm_fails tests/test_project_kickoff.py::test_send_kickoff_is_idempotent_when_already_sent -v
```

- [ ] **Step 3: Implement `project_kickoff_service.py`**

Create `backend/app/services/project_kickoff_service.py` with:

- `ProjectKickoffError`
- `_load_workflow(db, workflow_id, tenant_id)` — 404-style error if missing / wrong tenant / not `active` or missing `group_id`
- `_resolve_execution_context(db, workflow)`:
  - Resolve leader participant via `workflow.group_leader_agent_id` → Participant for that agent in the group
  - Resolve session via `group_chat_service.list_group_sessions(...)` — use the earliest non-deleted session (the provision-created「项目协作」session)
- `_llm_draft_kickoff(...)`:
  - Same model resolution pattern as `generate_team_building_proposals` (`load_active_model` + `create_llm_client`)
  - System prompt: write a Chinese kickoff from the user to the group leader, must start with `@{leader_name}`, include project goal, concrete first actions, ask leader to @ teammates and report back; plain text only
  - On any failure, raise and let caller fall back
- `draft_kickoff_message`: try LLM; on failure use `build_team_wakeup_message(workflow.team_plan)`
- `send_kickoff_message`:
  - If `workflow.kickoff_sent_at` set → return already_sent payload
  - `get_or_create_user_participant` for sender
  - Ensure content includes `@{leader_name}` prefix if missing
  - `group_message_service.enqueue_group_message(..., sender_participant_id=human.id, content=content, mention_participant_ids=[leader_participant_id])`
  - Set `workflow.kickoff_sent_at = datetime.now(UTC)`
  - Return `{group_id, session_id, message_id, already_sent: False}`

- [ ] **Step 4: Add API routes in `projects.py`**

```python
class KickoffDraftIn(BaseModel):
    instructions: str | None = Field(default=None, max_length=2000)


class KickoffDraftOut(BaseModel):
    content: str
    leader_participant_id: uuid.UUID
    leader_name: str
    group_id: uuid.UUID
    session_id: uuid.UUID


class KickoffSendIn(BaseModel):
    content: str = Field(min_length=1, max_length=12_000)


class KickoffSendOut(BaseModel):
    group_id: uuid.UUID
    session_id: uuid.UUID
    message_id: uuid.UUID | None = None
    already_sent: bool = False


@router.post("/{workflow_id}/kickoff/draft", response_model=KickoffDraftOut)
async def kickoff_draft(...):
    try:
        return await draft_kickoff_message(...)
    except ProjectKickoffError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{workflow_id}/kickoff/send", response_model=KickoffSendOut)
async def kickoff_send(...):
    try:
        return await send_kickoff_message(...)
    except ProjectKickoffError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

Register routes **before** any overly-greedy `/{workflow_id}/...` conflicts if needed (place near other `/{workflow_id}` routes).

- [ ] **Step 5: Frontend API helpers**

In `frontend/src/types/project.ts`:

```typescript
export interface KickoffDraft {
    content: string;
    leader_participant_id: string;
    leader_name: string;
    group_id: string;
    session_id: string;
}

export interface KickoffSendResult {
    group_id: string;
    session_id: string;
    message_id: string | null;
    already_sent: boolean;
}
```

In `frontend/src/services/projectApi.ts`:

```typescript
    kickoffDraft: (workflowId: string, data?: { instructions?: string }) =>
        fetchJson<KickoffDraft>(`/projects/${workflowId}/kickoff/draft`, {
            method: 'POST',
            body: JSON.stringify(data ?? {}),
        }),
    kickoffSend: (workflowId: string, content: string) =>
        fetchJson<KickoffSendResult>(`/projects/${workflowId}/kickoff/send`, {
            method: 'POST',
            body: JSON.stringify({ content }),
        }),
```

Update `hrReviewApi.selectProposal`:

```typescript
    selectProposal: (sessionId: string, proposalId: string, options?: { send_kickoff?: boolean }) =>
        fetchJson<TeamPlanSelection>(`/hr-review/sessions/${sessionId}/select`, {
            method: 'POST',
            body: JSON.stringify({
                proposal_id: proposalId,
                send_kickoff: options?.send_kickoff ?? true,
            }),
        }),
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
cd backend && pytest tests/test_project_kickoff.py -v
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/project_kickoff_service.py backend/app/api/projects.py \
  backend/tests/test_project_kickoff.py frontend/src/services/projectApi.ts \
  frontend/src/services/hrReviewApi.ts frontend/src/types/project.ts
git commit -m "$(cat <<'EOF'
feat: add project kickoff draft and send APIs

Let users generate an editable start message and send it as themselves while @mentioning the group leader.
EOF
)"
```

---

### Task 4: Projects four-step wizard UI

**Files:**
- Modify: `frontend/src/pages/Projects.tsx` (primary rewrite)
- Modify: `frontend/src/types/project.ts` (ensure proposals usable by `HrProposalCard`)
- Possibly import styles from `frontend/src/pages/groups/groups.css` for `.hr-proposal-*` or duplicate minimal styles inline/import

**Interfaces:**
- Consumes: `projectApi.buildTeamPlan`, `hrReviewApi.selectProposal(..., { send_kickoff: false })`, `projectApi.kickoffDraft`, `projectApi.kickoffSend`
- Produces: wizard state machine `draft | proposals_ready | proposal_selected | provisioned | kickoff_sent`

- [ ] **Step 1: Align proposal types for cards**

Ensure `TeamPlanProposal` / session proposals include fields `HrProposalCard` needs:

```typescript
export interface TeamPlanProposal {
    id: string;
    label: string;
    card_summary?: string;
    roles: ProjectTeamRole[];
}
```

Map API proposals into `HrTeamProposal[]` when rendering (fill `card_summary` with `label` fallback; ensure `duties`/`soul`/`suggested_tools` defaults).

- [ ] **Step 2: Rewrite `Projects.tsx` wizard**

Replace HR-board CTA block with:

1. **Step 1 — form:** `name`, `requirements`, button「生成方案」→ `projectApi.buildTeamPlan`; on success store `hrReviewSessionId` + proposals, advance.
2. **Step 2 — cards:** `<HrProposalCard proposals={...} onConfirm={async (id) => { setSelectedId(id); advance to proposal_selected }} />` — confirm here only selects locally (does **not** call select yet), OR call select only on Step 3. Spec: Step 2 selects, Step 3 creates. So Step 2 only stores `selectedProposalId` / proposal object; Step 3 calls API.
3. **Step 3 — create:** Show summary of selected proposal roles; button「创建团队」→ `hrReviewApi.selectProposal(hrSessionId, selectedId, { send_kickoff: false })`; store `workflow_id`, `group_id`, `session_id` from response; advance to provisioned. **Do not navigate.**
4. **Step 4 — kickoff:** On enter, `useEffect` → `projectApi.kickoffDraft(workflowId)` into textarea. Buttons:「重新生成文案」→ draft only;「生成并发送」→ if textarea empty draft first then `kickoffSend`; on success `navigate(/groups/${group_id}/${session_id})`.
5. Keep shareholder secondary button and projects list below.
6. List: if `status === 'active' && !kickoff_sent_at`, show「继续启动」→ jump wizard to Step 4 with that `workflow.id`.
7. Remove all `hrReviewApi.ensureBoard` /「去 HR 群提需求」UI and related `isHrReviewBoardGroup` usage from this page.

Error handling: keep name/requirements on generate failure; keep selection on create failure; keep textarea on send failure.

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npm run build
```

Expected: success (or only pre-existing unrelated errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Projects.tsx frontend/src/types/project.ts
git commit -m "$(cat <<'EOF'
feat: rebuild Projects page as team wizard

Replace HR-group chat entry with form → proposals → create → editable @leader kickoff send.
EOF
)"
```

---

### Task 5: Regression polish and verification

**Files:**
- Modify tests / MessageStream only if needed (chat select path should keep default `send_kickoff=True`)
- Verify no Projects CTA to HR board remains

- [ ] **Step 1: Confirm MessageStream still defaults to auto-kickoff**

`hrReviewApi.selectProposal(sessionId, proposalId)` without options → `send_kickoff: true`. No change required unless you previously changed the default. If chat path should also stop auto-kickoff later, that is out of scope — leave default True.

- [ ] **Step 2: Run backend suite for related tests**

```bash
cd backend && pytest tests/test_project_kickoff.py tests/test_hr_select_provisions_project.py tests/test_team_wakeup_no_time_rhythm.py -v
```

Expected: PASS (update any source-inspection tests if they assert on kickoff enqueue unconditionally).

- [ ] **Step 3: Grep for removed CTA**

```bash
rg "去 HR 群提需求" frontend/src
```

Expected: no matches.

- [ ] **Step 4: Manual checklist (document in commit body if useful)**

1. `/projects` shows form, not HR CTA  
2. Generate → ≥3 cards  
3. Confirm → Create → stays on page Step 4 with draft text  
4. Edit text → 生成并发送 → lands in execution group as user message @leader  
5. List「继续启动」for projects with null `kickoff_sent_at`  
6. Second send returns already_sent / no duplicate

- [ ] **Step 5: Final commit if any test fixes**

```bash
git add -A
git commit -m "$(cat <<'EOF'
test: verify kickoff split and wizard regressions

EOF
)"
```

Only commit if there are real changes.

---

## Spec Coverage Self-Review

| Spec requirement | Task |
|------------------|------|
| Form entry, remove HR CTA | Task 4 |
| One-shot LLM proposals via `/team-plans` | Task 4 |
| Confirm create without auto kickoff | Task 2 + Task 4 |
| Editable kickoff + 生成并发送 @群主 as user | Task 3 + Task 4 |
| `kickoff_sent_at` + continue start | Task 1 + Task 4 |
| Duplicate send `already_sent` | Task 3 |
| LLM draft with template fallback | Task 3 |
| Non-goals (Session B, HR chat entry) | Not implemented |

## Placeholder / consistency check

- `send_kickoff` default `True` everywhere except wizard `selectProposal(..., { send_kickoff: false })`.
- `KickoffSendOut.already_sent` matches frontend `KickoffSendResult.already_sent`.
- Alembic `down_revision` must match live head at implementation time.
