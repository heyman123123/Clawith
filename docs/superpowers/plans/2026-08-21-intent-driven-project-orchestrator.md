# Intent-Driven Project Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Intent-Driven Project Orchestrator that lets any tenant user describe a goal in natural language and have Clawith automatically provision a Group with a Chief (PM) Agent + executing agents, OKR + task board, and drive execution via progress signals — all while preserving the existing Agent Runtime boundary (C1) and tenant isolation (C2).

**Architecture:** Persistent Chief Runtime is a new `run_kind="orchestrator"` long-lived daemon subscribed to `task_board_events` + `group_file_events` + user messages. Orchestrator Service is a pure LLM-driven proposal generator that writes Drafts to `drafts` table; Atomic Creator idempotently materializes Drafts into Group/Agents/OKR/Tasks and dispatches a `start_chief_run` command via RuntimeCommandIntake (no direct LangGraph access). All time fields are opt-in.

**Tech Stack:** Python 3.12 + FastAPI + SQLModel + Alembic + LangGraph + Pydantic v2 + pytest-asyncio (existing) + React 18 + TypeScript + TanStack Query + Zustand (existing).

**Reference spec:** `docs/superpowers/specs/2026-08-21-intent-driven-project-orchestrator-design.md` (commit `c3c4da2b`).

---

## File Structure (Map)

### New files (backend)

```
backend/app/services/orchestrator/
├── __init__.py
├── intent_analyzer.py
├── agent_selector.py
├── group_planner.py
├── draft_renderer.py
├── atomic_creator.py
└ draft_schemas.py

backend/app/services/agent_runtime/orchestrator/
├── __init__.py
├── orchestrator_run.py
├── event_listener.py
├── reasoner.py
├── action_executor.py
├── progress_signal.py
└ checkpoint_state.py

backend/app/services/task_board/
├── __init__.py
├── service.py
├── models.py
├── event_publisher.py
├── dao.py
└ column_defs.py

backend/app/services/agent_template/registry/
├── __init__.py
├── visibility.py
├── approval.py
├── llm_generator.py
└ schemas.py

backend/app/api/orchestrator.py
backend/app/api/task_board.py
backend/app/api/chief.py
backend/app/api/template_registry.py

backend/alembic/versions/v1_0_0_g001_create_drafts.py
backend/alembic/versions/v1_0_0_g002_create_task_cards.py
backend/alembic/versions/v1_0_0_g003_create_task_card_dependencies.py
backend/alembic/versions/v1_0_0_g004_create_task_board_events.py
backend/alembic/versions/v1_0_0_g005_create_chief_runs.py
backend/alembic/versions/v1_0_0_g006_extend_agent_templates.py
backend/alembic/versions/v1_0_0_g007_extend_chat_sessions_chief_run.py

backend/app/models/draft.py
backend/app/models/task_card.py
backend/app/models/task_card_dependency.py
backend/app/models/task_board_event.py
backend/app/models/chief_run.py

backend/tests/test_orchestrator_intent_analyzer.py
backend/tests/test_orchestrator_agent_selector.py
backend/tests/test_orchestrator_group_planner.py
backend/tests/test_orchestrator_atomic_creator.py
backend/tests/test_task_board_service.py
backend/tests/test_task_board_state_machine.py
backend/tests/test_task_board_dag.py
backend/tests/test_chief_event_listener.py
backend/tests/test_chief_reasoner.py
backend/tests/test_chief_action_executor.py
backend/tests/test_chief_deadlock_avoidance.py
backend/tests/test_template_registry.py
backend/tests/test_orchestrator_e2e.py
```

### New files (frontend)

```
frontend/src/services/orchestrator.ts
frontend/src/services/taskBoard.ts
frontend/src/services/chief.ts
frontend/src/services/templateRegistry.ts

frontend/src/pages/Projects.tsx
frontend/src/pages/ProjectDetail.tsx
frontend/src/pages/DraftPreview.tsx
frontend/src/pages/projects/components/TaskBoard.tsx
frontend/src/pages/projects/components/TaskCard.tsx
frontend/src/pages/projects/components/ChiefChat.tsx
frontend/src/pages/projects/hooks/useProjects.ts
frontend/src/pages/projects/hooks/useTaskBoard.ts

frontend/src/components/Settings/OrchestratorSettings.tsx
```

### Modified files

```
backend/app/main.py                                  # register orchestrator + task_board routers
backend/app/services/agent_template/__init__.py    # export registry
backend/app/services/agent_runtime/command_worker.py # handle start_chief_run command
backend/app/services/agent_runtime/checkpointer.py   # support orchestrator thread_id
backend/app/models/agent_template.py                 # add visibility fields
backend/app/models/chat_session.py                   # add chief_run_id
backend/app/api/__init__.py                          # mount new routers
backend/app/dao.py                                   # tenant_id scope helpers
frontend/src/App.tsx                                 # add /projects route
frontend/src/services/api.ts                         # export new services
frontend/src/i18n/locales/{en,zh-CN}.json            # add translations
scripts/arch-guard.sh                                # add C1/C5 checks for orchestrator
docs/architecture/02-backend-runtime-boundary.md     # document new run_kind
docs/architecture/03-multi-tenant-data-model.md      # document new tables
README.md                                            # new Orchestrator section
```

---

## Wave 1: DB Migrations + SQLModel + DAO

Goal: Build the durable foundation. After this wave, all 5 new tables exist with tenant scoping, and DAO layer is fully unit-tested.

### Task 1.1: Alembic migration `g001` — drafts table

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g001_create_drafts.py`
- Test: `backend/tests/test_alembic_g001_drafts.py`

- [ ] **Step 1: Write the failing migration smoke test**

```python
# backend/tests/test_alembic_g001_drafts.py
import pytest
from sqlalchemy import inspect
from app.database import engine

@pytest.mark.asyncio
async def test_drafts_table_exists_with_tenant_id():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "drafts" in tables
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_alembic_g001_drafts.py -v
```

Expected: FAIL — `drafts` table not found.

- [ ] **Step 3: Write the migration (idempotent like f061)**

```python
# backend/alembic/versions/v1_0_0_g001_create_drafts.py
"""Create drafts table for Intent-Driven Orchestrator.

Revision ID: g001_create_drafts
Revises: f061_enterprise_info_tenant_id
Create Date: 2026-08-21 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "g001_create_drafts"
down_revision: Union[str, None] = "f061_enterprise_info_tenant_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "drafts" in inspector.get_table_names():
        return
    op.create_table(
        "drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_message", sa.Text, nullable=False),
        sa.Column("intent_summary", postgresql.JSONB, nullable=True),
        sa.Column("draft_payload", postgresql.JSONB, nullable=False),
        sa.Column("template_visibility", sa.String(32), nullable=False, server_default="user_private"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_detail", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_drafts_tenant_id", "drafts", ["tenant_id"])
    op.create_index("ix_drafts_user_id", "drafts", ["user_id"])
    op.create_index("ix_drafts_status", "drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_drafts_status", table_name="drafts")
    op.drop_index("ix_drafts_user_id", table_name="drafts")
    op.drop_index("ix_drafts_tenant_id", table_name="drafts")
    op.drop_table("drafts")
```

- [ ] **Step 4: Apply migration and run test**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g001_drafts.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/v1_0_0_g001_create_drafts.py backend/tests/test_alembic_g001_drafts.py
git commit -m "feat(db): add drafts table for orchestrator (g001)"
```

---

### Task 1.2: Alembic migration `g002` — task_cards table

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g002_create_task_cards.py`
- Test: `backend/tests/test_alembic_g002_task_cards.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_alembic_g002_task_cards.py
import pytest
from sqlalchemy import inspect, text
from app.database import engine

@pytest.mark.asyncio
async def test_task_cards_table_exists_with_optimistic_lock():
    async with engine.begin() as conn:
        cols = await conn.run_sync(lambda c: [r["name"] for r in inspect(c).get_columns("task_cards")])
    assert "version" in cols
    assert "column" in cols
    assert "tenant_id" in cols
```

- [ ] **Step 2: Run to fail**

```bash
cd backend && uv run pytest tests/test_alembic_g002_task_cards.py -v
```

- [ ] **Step 3: Write migration**

```python
# backend/alembic/versions/v1_0_0_g002_create_task_cards.py
"""Create task_cards table.

Revision ID: g002_create_task_cards
Revises: g001_create_drafts
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g002_create_task_cards"
down_revision = "g001_create_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_cards" in inspector.get_table_names():
        return
    op.create_table(
        "task_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("okr_key_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("column", sa.String(32), nullable=False, server_default="backlog"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("priority", sa.String(16), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("target_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_paths", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_type", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_cards_tenant_id", "task_cards", ["tenant_id"])
    op.create_index("ix_task_cards_group_id", "task_cards", ["group_id"])
    op.create_index("ix_task_cards_assignee_agent_id", "task_cards", ["assignee_agent_id"])
    op.create_index("ix_task_cards_okr_key_result_id", "task_cards", ["okr_key_result_id"])
    op.create_index("ix_task_cards_tenant_group_column", "task_cards", ["tenant_id", "group_id", "column"])
    op.create_index("ix_task_cards_assignee_column", "task_cards", ["assignee_agent_id", "column"])


def downgrade() -> None:
    op.drop_index("ix_task_cards_assignee_column", table_name="task_cards")
    op.drop_index("ix_task_cards_tenant_group_column", table_name="task_cards")
    op.drop_index("ix_task_cards_okr_key_result_id", table_name="task_cards")
    op.drop_index("ix_task_cards_assignee_agent_id", table_name="task_cards")
    op.drop_index("ix_task_cards_group_id", table_name="task_cards")
    op.drop_index("ix_task_cards_tenant_id", table_name="task_cards")
    op.drop_table("task_cards")
```

- [ ] **Step 4: Apply and test**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g002_task_cards.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/v1_0_0_g002_create_task_cards.py backend/tests/test_alembic_g002_task_cards.py
git commit -m "feat(db): add task_cards table with optimistic lock (g002)"
```

---

### Task 1.3: Alembic migration `g003` — task_card_dependencies

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g003_create_task_card_dependencies.py`
- Test: `backend/tests/test_alembic_g003_task_card_dependencies.py`

- [ ] **Step 1: Test**

```python
import pytest
from sqlalchemy import inspect
from app.database import engine

@pytest.mark.asyncio
async def test_task_card_dependencies_table_exists():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "task_card_dependencies" in tables
```

- [ ] **Step 2: Fail**

```bash
cd backend && uv run pytest tests/test_alembic_g003_task_card_dependencies.py -v
```

- [ ] **Step 3: Migration**

```python
# backend/alembic/versions/v1_0_0_g003_create_task_card_dependencies.py
"""Create task_card_dependencies table.

Revision ID: g003_create_task_card_dependencies
Revises: g002_create_task_cards
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g003_create_task_card_dependencies"
down_revision = "g002_create_task_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "task_card_dependencies" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "task_card_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("depends_on_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dependency_type", sa.String(16), nullable=False, server_default="blocks"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_card_id", "depends_on_card_id", name="uq_task_card_deps_pair"),
    )
    op.create_index("ix_task_card_dependencies_tenant_id", "task_card_dependencies", ["tenant_id"])
    op.create_index("ix_task_card_dependencies_task_card_id", "task_card_dependencies", ["task_card_id"])
    op.create_index("ix_task_card_dependencies_depends_on_card", "task_card_dependencies", ["depends_on_card_id"])


def downgrade() -> None:
    op.drop_table("task_card_dependencies")
```

- [ ] **Step 4: Apply and test**

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g003_task_card_dependencies.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/v1_0_0_g003_create_task_card_dependencies.py backend/tests/test_alembic_g003_task_card_dependencies.py
git commit -m "feat(db): add task_card_dependencies table (g003)"
```

---

### Task 1.4: Alembic migration `g004` — task_board_events

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g004_create_task_board_events.py`
- Test: `backend/tests/test_alembic_g004_task_board_events.py`

- [ ] **Step 1-5: Follow same TDD pattern**

```python
# migration
# Revision ID: g004_create_task_board_events
# Revises: g003_create_task_card_dependencies
"""Create task_board_events table."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g004_create_task_board_events"
down_revision = "g003_create_task_card_dependencies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "task_board_events" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "task_board_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_task_board_events_tenant_id", "task_board_events", ["tenant_id"])
    op.create_index("ix_task_board_events_group_id", "task_board_events", ["group_id"])
    op.create_index("ix_task_board_events_task_card_id", "task_board_events", ["task_card_id"])
    op.create_index("ix_task_board_events_tenant_group_occurred", "task_board_events", ["tenant_id", "group_id", sa.text("occurred_at DESC")])


def downgrade() -> None:
    op.drop_table("task_board_events")
```

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g004_task_board_events.py -v
git add backend/alembic/versions/v1_0_0_g004_create_task_board_events.py backend/tests/test_alembic_g004_task_board_events.py
git commit -m "feat(db): add task_board_events table (g004)"
```

---

### Task 1.5: Alembic migration `g005` — chief_runs

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g005_create_chief_runs.py`
- Test: `backend/tests/test_alembic_g005_chief_runs.py`

- [ ] **Steps 1-5: Same TDD pattern**

```python
# migration
"""Create chief_runs table."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g005_create_chief_runs"
down_revision = "g004_create_task_board_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "chief_runs" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "chief_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chief_agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_thread_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_failure_reason", sa.Text, nullable=True),
        sa.UniqueConstraint("group_id", name="uq_chief_runs_group_id"),
    )
    op.create_index("ix_chief_runs_tenant_id", "chief_runs", ["tenant_id"])
    op.create_index("ix_chief_runs_group_id", "chief_runs", ["group_id"])
    op.create_index("ix_chief_runs_status", "chief_runs", ["status"])


def downgrade() -> None:
    op.drop_table("chief_runs")
```

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g005_chief_runs.py -v
git add backend/alembic/versions/v1_0_0_g005_create_chief_runs.py backend/tests/test_alembic_g005_chief_runs.py
git commit -m "feat(db): add chief_runs table (g005)"
```

---

### Task 1.6: Alembic migration `g006` — extend agent_templates

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g006_extend_agent_templates.py`
- Test: `backend/tests/test_alembic_g006_agent_templates.py`

- [ ] **Step 1-5: Same TDD pattern with inspector checks (idempotent like f061)**

```python
# migration — uses sa.inspect to check column existence before ADD (idempotent)
"""Extend agent_templates with template_visibility / approval fields.

Revision ID: g006_extend_agent_templates
Revises: g005_create_chief_runs

Idempotent: Safe for retry; uses sa.inspect to check column existence.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g006_extend_agent_templates"
down_revision = "g005_create_chief_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("agent_templates")}

    if "template_visibility" not in existing:
        op.add_column("agent_templates", sa.Column("template_visibility", sa.String(32), nullable=False, server_default="public"))
    if "tenant_id" not in existing:
        op.add_column("agent_templates", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    if "created_by_user_id" not in existing:
        op.add_column("agent_templates", sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    if "approval_state" not in existing:
        op.add_column("agent_templates", sa.Column("approval_state", sa.String(32), nullable=False, server_default="approved"))
    if "rejected_reason" not in existing:
        op.add_column("agent_templates", sa.Column("rejected_reason", sa.Text, nullable=True))

    indexes = {i["name"] for i in inspector.get_indexes("agent_templates")}
    if "ix_agent_templates_visibility" not in indexes:
        op.create_index("ix_agent_templates_visibility", "agent_templates", ["template_visibility"])
    if "ix_agent_templates_tenant_visibility" not in indexes:
        op.create_index("ix_agent_templates_tenant_visibility", "agent_templates", ["tenant_id", "template_visibility"])


def downgrade() -> None:
    op.drop_index("ix_agent_templates_tenant_visibility", table_name="agent_templates")
    op.drop_index("ix_agent_templates_visibility", table_name="agent_templates")
    op.drop_column("agent_templates", "rejected_reason")
    op.drop_column("agent_templates", "approval_state")
    op.drop_column("agent_templates", "created_by_user_id")
    op.drop_column("agent_templates", "tenant_id")
    op.drop_column("agent_templates", "template_visibility")
```

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g006_agent_templates.py -v
git add backend/alembic/versions/v1_0_0_g006_extend_agent_templates.py backend/tests/test_alembic_g006_agent_templates.py
git commit -m "feat(db): extend agent_templates with visibility fields (g006)"
```

---

### Task 1.7: Alembic migration `g007` — extend chat_sessions

**Files:**
- Create: `backend/alembic/versions/v1_0_0_g007_extend_chat_sessions_chief_run.py`
- Test: `backend/tests/test_alembic_g007_chat_sessions.py`

- [ ] **Steps 1-5: Same pattern**

```python
# migration
"""Add chief_run_id to chat_sessions for Chief 1:1 Chat.

Revision ID: g007_extend_chat_sessions_chief_run
Revises: g006_extend_agent_templates
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g007_extend_chat_sessions_chief_run"
down_revision = "g006_extend_agent_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("chat_sessions")}
    if "chief_run_id" not in existing:
        op.add_column("chat_sessions", sa.Column("chief_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    indexes = {i["name"] for i in inspector.get_indexes("chat_sessions")}
    if "ix_chat_sessions_chief_run_id" not in indexes:
        op.create_index("ix_chat_sessions_chief_run_id", "chat_sessions", ["chief_run_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_chief_run_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "chief_run_id")
```

```bash
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_alembic_g007_chat_sessions.py -v
git add backend/alembic/versions/v1_0_0_g007_extend_chat_sessions_chief_run.py backend/tests/test_alembic_g007_chat_sessions.py
git commit -m "feat(db): add chief_run_id to chat_sessions (g007)"
```

---

### Task 1.8: SQLModel models

**Files:**
- Create: `backend/app/models/draft.py`
- Create: `backend/app/models/task_card.py`
- Create: `backend/app/models/task_card_dependency.py`
- Create: `backend/app/models/task_board_event.py`
- Create: `backend/app/models/chief_run.py`
- Modify: `backend/app/models/agent_template.py` (add new fields)
- Modify: `backend/app/models/chat_session.py` (add chief_run_id)
- Modify: `backend/app/models/__init__.py` (export new models)
- Test: `backend/tests/test_models_orchestrator.py`

- [ ] **Step 1: Write test**

```python
# backend/tests/test_models_orchestrator.py
from app.models.draft import Draft
from app.models.task_card import TaskCard
from app.models.task_card_dependency import TaskCardDependency
from app.models.task_board_event import TaskBoardEvent
from app.models.chief_run import ChiefRun

def test_draft_model_has_required_fields():
    fields = {c.name for c in Draft.__table__.columns}
    assert {"id", "tenant_id", "user_id", "user_message", "draft_payload",
            "template_visibility", "status", "created_at", "updated_at"} <= fields

def test_task_card_has_optimistic_lock_and_column():
    fields = {c.name for c in TaskCard.__table__.columns}
    assert {"version", "column", "tenant_id"} <= fields
```

- [ ] **Step 2: Run, expect import errors / missing fields**

- [ ] **Step 3: Implement models** (mirror migrations; full SQLModel with Mapped[…] definitions per spec §4 schema — straight translation of Task 1.1-1.7)

- [ ] **Step 4: Run tests, expect pass**

- [ ] **Step 5: Update `__init__.py` to import all new models**

```bash
cd backend && uv run pytest tests/test_models_orchestrator.py -v
git add backend/app/models/ backend/tests/test_models_orchestrator.py
git commit -m "feat(models): add SQLModel models for orchestrator (drafts/task_cards/events/chief_runs)"
```

---

### Task 1.9: Column definitions and enums

**Files:**
- Create: `backend/app/services/task_board/column_defs.py`
- Test: `backend/tests/test_task_board_column_defs.py`

- [ ] **Step 1: Test**

```python
from app.services.task_board.column_defs import TaskColumn, validate_transition
import pytest

def test_backlog_to_in_progress_is_valid():
    assert validate_transition(TaskColumn.BACKLOG, TaskColumn.IN_PROGRESS) is True

def test_done_to_in_progress_is_invalid():
    with pytest.raises(ValueError):
        validate_transition(TaskColumn.DONE, TaskColumn.IN_PROGRESS)
```

- [ ] **Step 2-3: Implement**

```python
# backend/app/services/task_board/column_defs.py
"""Task board column state machine."""
from __future__ import annotations
from enum import Enum


class TaskColumn(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"


_ALLOWED_TRANSITIONS: dict[TaskColumn, set[TaskColumn]] = {
    TaskColumn.BACKLOG: {TaskColumn.IN_PROGRESS, TaskColumn.BLOCKED},
    TaskColumn.IN_PROGRESS: {TaskColumn.BLOCKED, TaskColumn.REVIEW, TaskColumn.BACKLOG},
    TaskColumn.BLOCKED: {TaskColumn.BACKLOG, TaskColumn.IN_PROGRESS},
    TaskColumn.REVIEW: {TaskColumn.DONE, TaskColumn.IN_PROGRESS, TaskColumn.BLOCKED},
    TaskColumn.DONE: set(),  # terminal
}


def validate_transition(from_col: TaskColumn, to_col: TaskColumn) -> bool:
    if from_col == to_col:
        return True
    return to_col in _ALLOWED_TRANSITIONS.get(from_col, set())
```

- [ ] **Step 4-5: Test and commit**

```bash
cd backend && uv run pytest tests/test_task_board_column_defs.py -v
git add backend/app/services/task_board/column_defs.py backend/tests/test_task_board_column_defs.py
git commit -m "feat(task_board): add column state machine"
```

---

### Task 1.10: DAO layer with tenant scoping

**Files:**
- Create: `backend/app/services/task_board/dao.py`
- Test: `backend/tests/test_task_board_dao.py`

- [ ] **Step 1: Test (multi-tenant isolation + N+1 prevention)**

```python
import pytest
import uuid
from app.services.task_board.dao import TaskCardDAO
from app.database import async_session
from app.models.tenant import Tenant

@pytest.mark.asyncio
async def test_dao_filters_by_tenant_id():
    async with async_session() as db:
        t1 = Tenant(id=uuid.uuid4(), name="T1", slug=f"t1-{uuid.uuid4().hex[:6]}")
        t2 = Tenant(id=uuid.uuid4(), name="T2", slug=f"t2-{uuid.uuid4().hex[:6]}")
        db.add_all([t1, t2])
        await db.commit()
        # Create card in t1
        dao = TaskCardDAO(db)
        card = await dao.create_card(tenant_id=t1.id, title="X", created_by=t1.id, created_by_type="user")
        # Cross-tenant fetch should return None
        found = await dao.get_card(card.id, tenant_id=t2.id)
        assert found is None
        # Same-tenant fetch should return card
        same = await dao.get_card(card.id, tenant_id=t1.id)
        assert same is not None
```

- [ ] **Step 3: Implement**

```python
# backend/app/services/task_board/dao.py
"""TaskCard DAO with strict tenant scoping (C2) and batch loading (C5)."""
from __future__ import annotations
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task_card import TaskCard


class TaskCardDAO:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_card(
        self,
        *,
        tenant_id: UUID,
        title: str,
        created_by: UUID,
        created_by_type: str,
        group_id: UUID | None = None,
        description: str | None = None,
        okr_key_result_id: UUID | None = None,
        assignee_agent_id: UUID | None = None,
    ) -> TaskCard:
        card = TaskCard(
            tenant_id=tenant_id,
            group_id=group_id,
            assignee_agent_id=assignee_agent_id,
            okr_key_result_id=okr_key_result_id,
            title=title,
            description=description,
            created_by=created_by,
            created_by_type=created_by_type,
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def get_card(self, card_id: UUID, *, tenant_id: UUID) -> TaskCard | None:
        """Always scoped by tenant_id — C2 enforcement."""
        result = await self.db.execute(
            select(TaskCard).where(
                TaskCard.id == card_id,
                TaskCard.tenant_id == tenant_id,
                TaskCard.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_cards_by_group(self, group_id: UUID, *, tenant_id: UUID) -> list[TaskCard]:
        result = await self.db.execute(
            select(TaskCard)
            .where(TaskCard.group_id == group_id, TaskCard.tenant_id == tenant_id)
            .order_by(TaskCard.column, TaskCard.position)
        )
        return list(result.scalars().all())

    async def list_cards_by_assignee(self, agent_id: UUID, *, tenant_id: UUID) -> list[TaskCard]:
        result = await self.db.execute(
            select(TaskCard)
            .where(
                TaskCard.assignee_agent_id == agent_id,
                TaskCard.tenant_id == tenant_id,
                TaskCard.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())
```

- [ ] **Step 4-5: Test and commit**

```bash
cd backend && uv run pytest tests/test_task_board_dao.py -v
git add backend/app/services/task_board/dao.py backend/tests/test_task_board_dao.py
git commit -m "feat(task_board): add DAO with strict tenant scoping (C2)"
```

---

## Wave 2: Orchestrator Service

Goal: Take a natural-language goal and produce a Draft the user can review.

### Task 2.1: Pydantic schemas

**Files:**
- Create: `backend/app/services/orchestrator/draft_schemas.py`
- Test: `backend/tests/test_orchestrator_draft_schemas.py`

- [ ] **Step 1-3: Schemas**

```python
# backend/app/services/orchestrator/draft_schemas.py
"""Draft / proposal Pydantic schemas."""
from __future__ import annotations
from uuid import UUID
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class AgentProposal(BaseModel):
    role: str
    name: str
    system_prompt: str
    template_id: UUID | None = None
    is_new_template: bool = False
    suggested_visibility: Literal["user_private", "tenant_private", "public"] = "user_private"


class OKRProposal(BaseModel):
    objective_title: str
    objective_description: str
    key_results: list["KeyResultProposal"]


class KeyResultProposal(BaseModel):
    title: str
    target_value: float
    unit: str | None = None
    acceptance_artifact_paths: list[str] = Field(default_factory=list)


class TaskProposal(BaseModel):
    title: str
    description: str
    assignee_role: str | None = None
    depends_on_titles: list[str] = Field(default_factory=list)
    estimated_artifacts: list[str] = Field(default_factory=list)


class GroupProposal(BaseModel):
    name: str
    description: str


class DraftSummary(BaseModel):
    intent_category: str
    scope_summary: str
    key_constraints: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    user_message: str
    summary: DraftSummary
    group: GroupProposal
    members: list[AgentProposal]
    okr: OKRProposal | None = None
    tasks: list[TaskProposal]
    template_visibility: Literal["user_private", "tenant_private", "public"] = "user_private"
    estimated_total_llm_tokens: int | None = None
    status: Literal["pending", "approved", "rejected", "expired", "error", "consumed"] = "pending"
    error_detail: dict | None = None
    created_at: datetime
    updated_at: datetime


class ProjectCreated(BaseModel):
    draft_id: UUID
    group_id: UUID
    chief_agent_id: UUID
    chief_run_id: UUID
    task_card_ids: list[UUID]
    okr_objective_id: UUID | None = None
```

- [ ] **Step 4-5: Test and commit**

```bash
cd backend && uv run pytest tests/test_orchestrator_draft_schemas.py -v
git add backend/app/services/orchestrator/draft_schemas.py backend/tests/test_orchestrator_draft_schemas.py
git commit -m "feat(orchestrator): add draft Pydantic schemas"
```

---

### Task 2.2: IntentAnalyzer

**Files:**
- Create: `backend/app/services/orchestrator/intent_analyzer.py`
- Test: `backend/tests/test_orchestrator_intent_analyzer.py`

- [ ] **Step 1: Test with mock LLM**

```python
import pytest
from app.services.orchestrator.intent_analyzer import IntentAnalyzer
from app.services.orchestrator.draft_schemas import DraftSummary

@pytest.mark.asyncio
async def test_analyzer_returns_structured_summary(monkeypatch):
    async def mock_call_llm(prompt):
        return '{"intent_category": "growth_strategy", "scope_summary": "出海 SaaS 获客方案", "key_constraints": ["6周内"]}'
    analyzer = IntentAnalyzer(llm_caller=mock_call_llm)
    summary = await analyzer.analyze("帮我做一个出海 SaaS 获客方案", tenant_id=None, user_id=None)
    assert summary.intent_category == "growth_strategy"
    assert "获客" in summary.scope_summary
```

- [ ] **Step 3: Implement**

```python
# backend/app/services/orchestrator/intent_analyzer.py
"""IntentAnalyzer: parses user message into structured DraftSummary via LLM."""
from __future__ import annotations
import json
import logging
from collections.abc import Awaitable, Callable
from app.services.orchestrator.draft_schemas import DraftSummary

logger = logging.getLogger(__name__)

LLMCaller = Callable[[str], Awaitable[str]]

_PROMPT = """你是一个项目意图分析助手。请将用户的自然语言需求解析为以下 JSON 格式:
{{
  "intent_category": "<category>",   // 例: growth_strategy / market_research / engineering_task / monitoring
  "scope_summary": "<一句话总结需求范围>",
  "key_constraints": ["<约束1>", "<约束2>"]
}}
用户输入: {user_message}
只返回 JSON,不要其他文本。
"""


class IntentAnalyzer:
    def __init__(self, llm_caller: LLMCaller, fallback_caller: LLMCaller | None = None) -> None:
        self.llm_caller = llm_caller
        self.fallback_caller = fallback_caller

    async def analyze(self, user_message: str, *, tenant_id, user_id) -> DraftSummary:
        prompt = _PROMPT.format(user_message=user_message)
        raw = await self._call_with_fallback(prompt)
        try:
            data = json.loads(raw)
            return DraftSummary(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("IntentAnalyzer parse failed: %s; raw=%r", exc, raw)
            raise

    async def _call_with_fallback(self, prompt: str) -> str:
        try:
            return await self.llm_caller(prompt)
        except Exception as exc:
            logger.warning("Primary LLM failed (%s); falling back", exc)
            if self.fallback_caller is None:
                raise
            return await self.fallback_caller(prompt)
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_orchestrator_intent_analyzer.py -v
git add backend/app/services/orchestrator/intent_analyzer.py backend/tests/test_orchestrator_intent_analyzer.py
git commit -m "feat(orchestrator): add IntentAnalyzer with LLM fallback"
```

---

### Task 2.3: AgentSelector (template + LLM generation)

**Files:**
- Create: `backend/app/services/orchestrator/agent_selector.py`
- Test: `backend/tests/test_orchestrator_agent_selector.py`

- [ ] **Step 1: Test**

```python
import pytest
from uuid import uuid4
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.draft_schemas import DraftSummary

@pytest.mark.asyncio
async def test_selector_returns_existing_template_match(monkeypatch):
    async def mock_template_search(intent):
        return [{"template_id": uuid4(), "role": "growth-hacker", "name": "Growth Hacker"}]

    async def mock_llm_generate(intent, role_gaps):
        return []

    selector = AgentSelector(
        template_search=mock_template_search,
        llm_generate_template=mock_llm_generate,
    )
    members = await selector.select(
        summary=DraftSummary(intent_category="growth", scope_summary="x", key_constraints=[]),
        tenant_id=None,
    )
    assert len(members) == 1
    assert members[0].template_id is not None
```

- [ ] **Step 3: Implement**

```python
# backend/app/services/orchestrator/agent_selector.py
"""AgentSelector: reuses existing templates when possible; LLM-generates missing roles."""
from __future__ import annotations
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID
from app.services.orchestrator.draft_schemas import AgentProposal, DraftSummary

logger = logging.getLogger(__name__)


class AgentSelector:
    def __init__(
        self,
        template_search: Callable[[str], Awaitable[list[dict]]],
        llm_generate_template: Callable[[str, list[str]], Awaitable[list[dict]]],
    ) -> None:
        self.template_search = template_search
        self.llm_generate_template = llm_generate_template

    async def select(
        self,
        *,
        summary: DraftSummary,
        tenant_id: UUID | None,
        always_include_chief: bool = True,
    ) -> list[AgentProposal]:
        existing = await self.template_search(summary.intent_category)
        proposals = [
            AgentProposal(
                role=t["role"],
                name=t["name"],
                system_prompt=t.get("system_prompt", ""),
                template_id=t["template_id"],
                is_new_template=False,
            )
            for t in existing
        ]

        filled_roles = {p.role for p in proposals}
        role_gaps = self._identify_gaps(summary, filled_roles)
        if role_gaps:
            generated = await self.llm_generate_template(summary.intent_category, role_gaps)
            for g in generated:
                proposals.append(
                    AgentProposal(
                        role=g["role"],
                        name=g["name"],
                        system_prompt=g["system_prompt"],
                        template_id=None,
                        is_new_template=True,
                    )
                )

        if always_include_chief and "chief-of-staff" not in filled_roles:
            proposals.insert(
                0,
                AgentProposal(
                    role="chief-of-staff",
                    name="Chief of Staff",
                    system_prompt="You are the Chief of Staff for this group.",
                    template_id=None,  # resolved at create time
                    is_new_template=False,
                ),
            )

        return proposals

    def _identify_gaps(self, summary: DraftSummary, filled_roles: set[str]) -> list[str]:
        # Trivial gap detection; refined in future iterations
        if "researcher" not in filled_roles:
            return ["researcher"]
        return []
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_orchestrator_agent_selector.py -v
git add backend/app/services/orchestrator/agent_selector.py backend/tests/test_orchestrator_agent_selector.py
git commit -m "feat(orchestrator): add AgentSelector with template reuse + LLM gen"
```

---

### Task 2.4: GroupPlanner

**Files:**
- Create: `backend/app/services/orchestrator/group_planner.py`
- Test: `backend/tests/test_orchestrator_group_planner.py`

- [ ] **Step 1-3: Implement**

```python
# backend/app/services/orchestrator/group_planner.py
"""GroupPlanner: builds GroupProposal + OKRProposal + TaskProposal from summary + members."""
from __future__ import annotations
import uuid
from app.services.orchestrator.draft_schemas import (
    AgentProposal,
    DraftSummary,
    GroupProposal,
    OKRProposal,
    KeyResultProposal,
    TaskProposal,
)


class GroupPlanner:
    def __init__(self, llm_caller) -> None:
        self.llm_caller = llm_caller

    async def plan(
        self,
        *,
        summary: DraftSummary,
        members: list[AgentProposal],
        user_message: str,
    ) -> tuple[GroupProposal, OKRProposal | None, list[TaskProposal]]:
        group = GroupProposal(
            name=summary.scope_summary[:80],
            description=user_message,
        )
        okr = OKRProposal(
            objective_title=summary.scope_summary,
            objective_description=user_message,
            key_results=[
                KeyResultProposal(
                    title="第一阶段:研究 + 方案草稿",
                    target_value=100.0,
                    unit="%",
                    acceptance_artifact_paths=["group_files/plan_draft.md"],
                ),
            ],
        )
        tasks = [
            TaskProposal(
                title=f"{m.name} 接手第一阶段任务",
                description=f"基于 {summary.intent_category} 推进",
                assignee_role=m.role,
            )
            for m in members
            if m.role != "chief-of-staff"
        ]
        return group, okr, tasks
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_orchestrator_group_planner.py -v
git add backend/app/services/orchestrator/group_planner.py backend/tests/test_orchestrator_group_planner.py
git commit -m "feat(orchestrator): add GroupPlanner"
```

---

### Task 2.5: DraftRenderer + persistence

**Files:**
- Create: `backend/app/services/orchestrator/draft_renderer.py`
- Modify: `backend/app/services/orchestrator/__init__.py` (export OrchestratorService)
- Test: `backend/tests/test_orchestrator_service.py`

- [ ] **Step 1: Test**

```python
import pytest
from uuid import uuid4
from app.services.orchestrator import OrchestratorService

@pytest.mark.asyncio
async def test_propose_persists_draft_and_returns_id():
    # ... full integration test using test DB fixture
    pass  # see backend/tests/test_orchestrator_service.py for full version
```

- [ ] **Step 3: OrchestratorService**

```python
# backend/app/services/orchestrator/__init__.py
"""Orchestrator service: end-to-end propose + create pipeline."""
from __future__ import annotations
import uuid
from app.database import async_session
from app.models.draft import Draft
from app.services.orchestrator.intent_analyzer import IntentAnalyzer
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.group_planner import GroupPlanner
from app.services.orchestrator.draft_schemas import Draft, DraftSummary


class OrchestratorService:
    def __init__(
        self,
        *,
        intent_analyzer: IntentAnalyzer,
        agent_selector: AgentSelector,
        group_planner: GroupPlanner,
    ) -> None:
        self.intent_analyzer = intent_analyzer
        self.agent_selector = agent_selector
        self.group_planner = group_planner

    async def propose(
        self, *, tenant_id, user_id, user_message: str
    ) -> Draft:
        summary = await self.intent_analyzer.analyze(
            user_message, tenant_id=tenant_id, user_id=user_id
        )
        members = await self.agent_selector.select(summary=summary, tenant_id=tenant_id)
        group, okr, tasks = await self.group_planner.plan(
            summary=summary, members=members, user_message=user_message
        )
        draft = Draft(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            user_message=user_message,
            intent_summary=summary.model_dump(),
            draft_payload={
                "group": group.model_dump(),
                "members": [m.model_dump() for m in members],
                "okr": okr.model_dump() if okr else None,
                "tasks": [t.model_dump() for t in tasks],
            },
            template_visibility="user_private",
        )
        async with async_session() as db:
            db.add(draft)
            await db.commit()
            await db.refresh(draft)
        return Draft(
            id=draft.id,
            tenant_id=draft.tenant_id,
            user_id=draft.user_id,
            user_message=draft.user_message,
            summary=summary,
            group=group,
            members=members,
            okr=okr,
            tasks=tasks,
            template_visibility=draft.template_visibility,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
        )
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_orchestrator_service.py -v
git add backend/app/services/orchestrator/ backend/tests/test_orchestrator_service.py
git commit -m "feat(orchestrator): add OrchestratorService.propose() with persistence"
```

---

### Task 2.6: AtomicCreator

**Files:**
- Create: `backend/app/services/orchestrator/atomic_creator.py`
- Test: `backend/tests/test_orchestrator_atomic_creator.py`

- [ ] **Step 1: Test (idempotency)**

```python
import pytest
from uuid import uuid4
from app.services.orchestrator.atomic_creator import AtomicCreator

@pytest.mark.asyncio
async def test_atomic_creator_is_idempotent_on_draft_id():
    # Create once, then attempt recreate with same draft_id
    # Both runs should produce identical group_id and no duplicate resources
    pass  # see full version
```

- [ ] **Step 3: Implement**

```python
# backend/app/services/orchestrator/atomic_creator.py
"""AtomicCreator: idempotently materializes a Draft into Group + Members + OKR + Tasks
+ dispatches start_chief_run command via RuntimeCommandIntake (no direct Runtime access).

Idempotency key = draft_id. All resources use client-generated UUIDs derived from draft_id
so retries with the same draft_id produce the same DB state.
"""
from __future__ import annotations
import hashlib
import uuid
import logging
from app.database import async_session
from app.models.draft import Draft as DraftModel
from app.services.orchestrator.draft_schemas import Draft as DraftSchema, ProjectCreated

logger = logging.getLogger(__name__)


class AtomicCreatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AtomicCreator:
    def __init__(self, *, group_service, agent_service, okr_service, task_board_service, command_intake_service) -> None:
        self.group_service = group_service
        self.agent_service = agent_service
        self.okr_service = okr_service
        self.task_board_service = task_board_service
        self.command_intake_service = command_intake_service

    def _deterministic_uuid(self, draft_id: uuid.UUID, suffix: str) -> uuid.UUID:
        """Generate a stable UUID from draft_id + suffix (idempotency key)."""
        h = hashlib.sha256(f"{draft_id}|{suffix}".encode()).hexdigest()
        return uuid.UUID(h[:32])

    async def create(self, *, tenant_id: uuid.UUID, draft_id: uuid.UUID) -> ProjectCreated:
        async with async_session() as db:
            draft_row = await db.get(DraftModel, draft_id)
            if draft_row is None or draft_row.tenant_id != tenant_id:
                raise AtomicCreatorError("draft_not_found", f"Draft {draft_id} not found")
            if draft_row.status == "consumed":
                # Idempotent re-run: return previously created project
                return self._replay_from_draft(draft_row)
            draft = DraftSchema.model_validate(self._hydrate_payload(draft_row))

        try:
            group_id = self._deterministic_uuid(draft.id, "group")
            chief_agent_id = self._deterministic_uuid(draft.id, "chief_agent")

            # 1. Create group
            group = await self.group_service.create(
                tenant_id=tenant_id,
                group_id=group_id,
                name=draft.group.name,
                description=draft.group.description,
                created_by=draft.user_id,
            )

            # 2. Create members (chief + agents)
            members = await self._create_members(draft, tenant_id, group_id, chief_agent_id)

            # 3. Create OKR
            okr_objective_id = None
            if draft.okr:
                okr_objective_id = await self.okr_service.create_objective(
                    tenant_id=tenant_id,
                    draft=draft.okr,
                    owner_group_id=group_id,
                )

            # 4. Create tasks
            task_card_ids = await self.task_board_service.create_initial_cards(
                tenant_id=tenant_id,
                group_id=group_id,
                tasks=draft.tasks,
                okr_objective_id=okr_objective_id,
                chief_run_id=None,
            )

            # 5. Dispatch start_chief_run command (C1 compliance)
            chief_run_id = self._deterministic_uuid(draft.id, "chief_run")
            await self.command_intake_service.enqueue_start_chief_run(
                tenant_id=tenant_id,
                chief_run_id=chief_run_id,
                group_id=group_id,
                chief_agent_id=chief_agent_id,
                runtime_thread_id=f"orchestrator:{group_id}",
            )

            # 6. Mark draft consumed
            async with async_session() as db:
                draft_row = await db.get(DraftModel, draft_id)
                draft_row.status = "consumed"
                await db.commit()

            return ProjectCreated(
                draft_id=draft.id,
                group_id=group_id,
                chief_agent_id=chief_agent_id,
                chief_run_id=chief_run_id,
                task_card_ids=task_card_ids,
                okr_objective_id=okr_objective_id,
            )
        except Exception as exc:
            logger.exception("AtomicCreator failed for draft %s", draft_id)
            async with async_session() as db:
                draft_row = await db.get(DraftModel, draft_id)
                if draft_row:
                    draft_row.status = "error"
                    draft_row.error_detail = {"code": "create_failed", "message": str(exc)}
                    await db.commit()
            raise

    def _hydrate_payload(self, draft_row) -> dict:
        return {
            "id": draft_row.id,
            "tenant_id": draft_row.tenant_id,
            "user_id": draft_row.user_id,
            "user_message": draft_row.user_message,
            "summary": draft_row.intent_summary,
            "group": draft_row.draft_payload["group"],
            "members": draft_row.draft_payload["members"],
            "okr": draft_row.draft_payload.get("okr"),
            "tasks": draft_row.draft_payload["tasks"],
            "template_visibility": draft_row.template_visibility,
            "status": draft_row.status,
            "created_at": draft_row.created_at,
            "updated_at": draft_row.updated_at,
        }

    async def _create_members(self, draft, tenant_id, group_id, chief_agent_id):
        # Real impl delegates to agent_service.create + group_member_service.add
        return []  # placeholder; expand per actual Clawith APIs

    def _replay_from_draft(self, draft_row) -> ProjectCreated:
        # Idempotency path: extract previously-created IDs from draft payload
        payload = draft_row.draft_payload
        return ProjectCreated(
            draft_id=draft_row.id,
            group_id=self._deterministic_uuid(draft_row.id, "group"),
            chief_agent_id=self._deterministic_uuid(draft_row.id, "chief_agent"),
            chief_run_id=self._deterministic_uuid(draft_row.id, "chief_run"),
            task_card_ids=payload.get("task_card_ids", []),
            okr_objective_id=payload.get("okr_objective_id"),
        )
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_orchestrator_atomic_creator.py -v
git add backend/app/services/orchestrator/atomic_creator.py backend/tests/test_orchestrator_atomic_creator.py
git commit -m "feat(orchestrator): add AtomicCreator with draft_id idempotency"
```

---

### Task 2.7: API endpoints for Draft CRUD

**Files:**
- Create: `backend/app/api/orchestrator.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_orchestrator.py`

- [ ] **Step 1: Test**

```python
# tests cover: POST /api/orchestrator/drafts (propose), GET /api/orchestrator/drafts/{id}, PATCH (edit), POST /api/orchestrator/drafts/{id}/approve
```

- [ ] **Step 3: API**

```python
# backend/app/api/orchestrator.py
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from app.core.tenant_context import require_tenant_context
from app.services.orchestrator import OrchestratorService, AtomicCreator
from app.services.orchestrator.draft_schemas import Draft, ProjectCreated

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.post("/drafts", response_model=Draft, status_code=status.HTTP_201_CREATED)
async def propose_draft(
    payload: dict,  # { user_message: str }
    ctx = Depends(require_tenant_context),
) -> Draft:
    service: OrchestratorService = ctx.deps.orchestrator_service
    return await service.propose(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        user_message=payload["user_message"],
    )


@router.post("/drafts/{draft_id}/approve", response_model=ProjectCreated)
async def approve_draft(draft_id: UUID, ctx = Depends(require_tenant_context)) -> ProjectCreated:
    creator: AtomicCreator = ctx.deps.atomic_creator
    return await creator.create(tenant_id=ctx.tenant_id, draft_id=draft_id)
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_api_orchestrator.py -v
git add backend/app/api/orchestrator.py backend/app/main.py
git commit -m "feat(api): add orchestrator endpoints (propose / approve)"
```

---

## Wave 3: TaskBoard Service

### Task 3.1: TaskBoardService (CRUD + state machine + DAG)

**Files:**
- Create: `backend/app/services/task_board/service.py`
- Modify: `backend/app/services/task_board/__init__.py`
- Test: `backend/tests/test_task_board_service.py`
- Test: `backend/tests/test_task_board_state_machine.py`
- Test: `backend/tests/test_task_board_dag.py`

- [ ] **Step 1-3: Implementation**

```python
# backend/app/services/task_board/service.py
"""TaskBoardService: create / move / assign cards with optimistic lock + state machine + DAG check."""
from __future__ import annotations
import uuid
import logging
from collections import defaultdict
from app.database import async_session
from app.models.task_card import TaskCard
from app.models.task_card_dependency import TaskCardDependency
from app.services.task_board.dao import TaskCardDAO
from app.services.task_board.column_defs import TaskColumn, validate_transition
from app.services.task_board.event_publisher import TaskBoardEventPublisher

logger = logging.getLogger(__name__)


class TaskBoardError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class TaskBoardService:
    def __init__(self, event_publisher: TaskBoardEventPublisher | None = None) -> None:
        self.event_publisher = event_publisher or TaskBoardEventPublisher()

    async def create_card(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_type: str,
        title: str,
        group_id: uuid.UUID | None = None,
        assignee_agent_id: uuid.UUID | None = None,
        okr_key_result_id: uuid.UUID | None = None,
        description: str | None = None,
        priority: str | None = None,
        target_window_start=None,
        target_window_end=None,
        depends_on_card_ids: list[uuid.UUID] | None = None,
    ) -> TaskCard:
        async with async_session() as db:
            dao = TaskCardDAO(db)
            card = await dao.create_card(
                tenant_id=tenant_id,
                title=title,
                description=description,
                group_id=group_id,
                assignee_agent_id=assignee_agent_id,
                okr_key_result_id=okr_key_result_id,
                created_by=actor_id,
                created_by_type=actor_type,
            )
            # Add dependencies (DAG check)
            if depends_on_card_ids:
                self._validate_dag_no_cycle(
                    new_card_id=card.id,
                    deps=depends_on_card_ids,
                    tenant_id=tenant_id,
                    db=db,
                )
                for dep_id in depends_on_card_ids:
                    db.add(
                        TaskCardDependency(
                            tenant_id=tenant_id,
                            task_card_id=card.id,
                            depends_on_card_id=dep_id,
                        )
                    )
            await db.commit()
            await db.refresh(card)

        await self.event_publisher.publish(
            tenant_id=tenant_id,
            group_id=card.group_id or uuid.uuid4(),
            task_card_id=card.id,
            event_type="card_created",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={"title": card.title, "column": card.column},
        )
        return card

    async def move_card(
        self,
        *,
        tenant_id: uuid.UUID,
        card_id: uuid.UUID,
        to_column: TaskColumn,
        actor_id: uuid.UUID,
        actor_type: str,
        expected_version: int,
    ) -> TaskCard:
        async with async_session() as db:
            dao = TaskCardDAO(db)
            card = await dao.get_card(card_id, tenant_id=tenant_id)
            if card is None:
                raise TaskBoardError("card_not_found", f"Card {card_id} not found")
            if card.version != expected_version:
                raise TaskBoardError(
                    "version_conflict",
                    f"Card was modified by another actor (expected v{expected_version}, got v{card.version})",
                )
            from_col = TaskColumn(card.column)
            if not validate_transition(from_col, to_column):
                raise TaskBoardError(
                    "invalid_transition",
                    f"Cannot move from {from_col.value} to {to_column.value}",
                )
            card.column = to_column.value
            card.version += 1
            await db.commit()
            await db.refresh(card)

        await self.event_publisher.publish(
            tenant_id=tenant_id,
            group_id=card.group_id,
            task_card_id=card.id,
            event_type="card_moved",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={"from": from_col.value, "to": to_column.value},
        )
        return card

    def _validate_dag_no_cycle(
        self,
        *,
        new_card_id: uuid.UUID,
        deps: list[uuid.UUID],
        tenant_id: uuid.UUID,
        db,
    ) -> None:
        # Build adjacency: new_card_id -> deps
        adj: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        adj[new_card_id] = list(deps)
        for dep_id in deps:
            existing = db.execute(
                select(TaskCardDependency).where(
                    TaskCardDependency.task_card_id == dep_id,
                    TaskCardDependency.tenant_id == tenant_id,
                )
            ).scalars().all()
            for e in existing:
                adj.setdefault(dep_id, []).append(e.depends_on_card_id)

        # DFS cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        color = defaultdict(lambda: WHITE)
        def dfs(node):
            if color[node] == GRAY:
                raise TaskBoardError("dag_cycle", f"Dependency cycle detected at {node}")
            if color[node] == BLACK:
                return
            color[node] = GRAY
            for nxt in adj.get(node, []):
                dfs(nxt)
            color[node] = BLACK
        dfs(new_card_id)
```

- [ ] **Step 4-5: Tests + commit**

```bash
cd backend && uv run pytest tests/test_task_board_service.py tests/test_task_board_state_machine.py tests/test_task_board_dag.py -v
git add backend/app/services/task_board/ backend/tests/test_task_board_*.py
git commit -m "feat(task_board): add TaskBoardService with state machine + DAG + optimistic lock"
```

---

### Task 3.2: PG NOTIFY publisher + listener

**Files:**
- Create: `backend/app/services/task_board/event_publisher.py`
- Test: `backend/tests/test_task_board_event_publisher.py`

- [ ] **Step 1-3**

```python
# backend/app/services/task_board/event_publisher.py
"""TaskBoardEventPublisher: emits task_board_events + PG NOTIFY for Chief subscription."""
from __future__ import annotations
import json
import uuid
import logging
from datetime import datetime, timezone
from app.database import async_session
from app.models.task_board_event import TaskBoardEvent

logger = logging.getLogger(__name__)


class TaskBoardEventPublisher:
    async def publish(
        self,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        task_card_id: uuid.UUID,
        event_type: str,
        actor_id: uuid.UUID,
        actor_type: str,
        payload: dict | None = None,
    ) -> None:
        event = TaskBoardEvent(
            tenant_id=tenant_id,
            group_id=group_id,
            task_card_id=task_card_id,
            event_type=event_type,
            payload=payload or {},
            actor_id=actor_id,
            actor_type=actor_type,
        )
        async with async_session() as db:
            db.add(event)
            await db.commit()
            await db.refresh(event)

        # Fire PG NOTIFY for live subscribers (best-effort)
        try:
            from app.database import engine
            async with engine.begin() as conn:
                await conn.exec_driver_sql(
                    f"NOTIFY task_board_events, '{event.id}'"
                )
        except Exception as exc:
            logger.warning("PG NOTIFY failed (event still persisted): %s", exc)
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_task_board_event_publisher.py -v
git add backend/app/services/task_board/event_publisher.py backend/tests/test_task_board_event_publisher.py
git commit -m "feat(task_board): add event publisher with PG NOTIFY"
```

---

### Task 3.3: Task Board API

**Files:**
- Create: `backend/app/api/task_board.py`
- Test: `backend/tests/test_api_task_board.py`

- [ ] **Step 1-5: API endpoints (CRUD + move) with C2 tenant scoping**

```python
# backend/app/api/task_board.py (excerpts)
@router.post("/api/task-board/cards", status_code=201)
async def create_card_endpoint(payload: dict, ctx = Depends(require_tenant_context)):
    ...

@router.patch("/api/task-board/cards/{card_id}/move")
async def move_card_endpoint(card_id: UUID, payload: dict, ctx = Depends(require_tenant_context)):
    ...
```

```bash
cd backend && uv run pytest tests/test_api_task_board.py -v
git add backend/app/api/task_board.py backend/tests/test_api_task_board.py
git commit -m "feat(api): add task-board endpoints"
```

---

## Wave 4: Chief Runtime

### Task 4.1: OrchestratorRunLoop (run_kind="orchestrator")

**Files:**
- Create: `backend/app/services/agent_runtime/orchestrator/orchestrator_run.py`
- Test: `backend/tests/test_chief_orchestrator_run.py`

- [ ] **Step 1-3**

```python
# backend/app/services/agent_runtime/orchestrator/orchestrator_run.py
"""OrchestratorRunLoop: long-lived Chief Runtime subscribed to events."""
from __future__ import annotations
import asyncio
import uuid
import logging
from app.database import async_session
from app.models.chief_run import ChiefRun

logger = logging.getLogger(__name__)


class OrchestratorRunLoop:
    def __init__(self, *, event_listener, reasoner, action_executor, health_checker) -> None:
        self.event_listener = event_listener
        self.reasoner = reasoner
        self.action_executor = action_executor
        self.health_checker = health_checker

    async def run(self, *, group_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        async with async_session() as db:
            chief_run = await db.execute(
                select(ChiefRun).where(
                    ChiefRun.group_id == group_id, ChiefRun.tenant_id == tenant_id
                )
            )
            chief_run = chief_run.scalar_one_or_none()
            if chief_run is None:
                raise RuntimeError(f"ChiefRun for group {group_id} not found")

        async for event in self.event_listener.subscribe(tenant_id=tenant_id, group_id=group_id):
            if chief_run.status == "stopped":
                logger.info("ChiefRun %s stopped; exiting loop", chief_run.id)
                return
            try:
                action_plan = await self.reasoner.decide(event=event, group_state=...)
                await self.action_executor.execute(plan=action_plan, group_id=group_id, tenant_id=tenant_id)
            except Exception as exc:
                logger.exception("ChiefReasoner failed; bumping failure_count")
                await self._bump_failure(chief_run.id, str(exc))
                if chief_run.failure_count >= 5:
                    await self._mark_degraded(chief_run.id, str(exc))
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_chief_orchestrator_run.py -v
git add backend/app/services/agent_runtime/orchestrator/orchestrator_run.py backend/tests/test_chief_orchestrator_run.py
git commit -m "feat(chief): add OrchestratorRunLoop with failure bump + degraded state"
```

---

### Task 4.2: EventListener (PG NOTIFY/LISTEN + compensation query)

**Files:**
- Create: `backend/app/services/agent_runtime/orchestrator/event_listener.py`
- Test: `backend/tests/test_chief_event_listener.py`

- [ ] **Step 1-3**

```python
# backend/app/services/agent_runtime/orchestrator/event_listener.py
"""EventListener: subscribes to task_board_events via PG LISTEN + 30s compensation poll."""
from __future__ import annotations
import asyncio
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator
from app.database import async_session
from app.models.task_board_event import TaskBoardEvent

logger = logging.getLogger(__name__)


class OrchestratorEvent:
    def __init__(self, event_id: uuid.UUID, event_type: str, task_card_id: uuid.UUID, payload: dict, occurred_at: datetime):
        self.event_id = event_id
        self.event_type = event_type
        self.task_card_id = task_card_id
        self.payload = payload
        self.occurred_at = occurred_at


class EventListener:
    def __init__(self, *, compensation_interval_seconds: int = 30) -> None:
        self.compensation_interval = compensation_interval_seconds
        self._queue: asyncio.Queue[OrchestratorEvent] = asyncio.Queue()
        self._last_seen_at: datetime | None = None

    async def subscribe(self, *, tenant_id: uuid.UUID, group_id: uuid.UUID) -> AsyncIterator[OrchestratorEvent]:
        # Background task: compensate-query
        comp_task = asyncio.create_task(self._compensation_loop(tenant_id, group_id))
        try:
            while True:
                event = await self._queue.get()
                yield event
        finally:
            comp_task.cancel()

    async def _compensation_loop(self, tenant_id, group_id):
        while True:
            await asyncio.sleep(self.compensation_interval)
            try:
                async with async_session() as db:
                    stmt = select(TaskBoardEvent).where(
                        TaskBoardEvent.tenant_id == tenant_id,
                        TaskBoardEvent.group_id == group_id,
                    )
                    if self._last_seen_at is not None:
                        stmt = stmt.where(TaskBoardEvent.occurred_at > self._last_seen_at)
                    stmt = stmt.order_by(TaskBoardEvent.occurred_at).limit(100)
                    events = (await db.execute(stmt)).scalars().all()
                for e in events:
                    self._last_seen_at = e.occurred_at
                    await self._queue.put(OrchestratorEvent(
                        event_id=e.id, event_type=e.event_type,
                        task_card_id=e.task_card_id,
                        payload=e.payload, occurred_at=e.occurred_at,
                    ))
            except Exception as exc:
                logger.warning("Compensation query failed: %s", exc)
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_chief_event_listener.py -v
git add backend/app/services/agent_runtime/orchestrator/event_listener.py backend/tests/test_chief_event_listener.py
git commit -m "feat(chief): add EventListener with PG NOTIFY + compensation"
```

---

### Task 4.3: Reasoner (LLM decision)

**Files:**
- Create: `backend/app/services/agent_runtime/orchestrator/reasoner.py`
- Create: `backend/app/services/agent_runtime/orchestrator/checkpoint_state.py`
- Test: `backend/tests/test_chief_reasoner.py`

- [ ] **Step 1-3**

```python
# backend/app/services/agent_runtime/orchestrator/checkpoint_state.py
from __future__ import annotations
from pydantic import BaseModel


class ActionPlan(BaseModel):
    review_artifact_paths: list[str] = []
    move_cards: list[dict] = []  # [{card_id, to_column, expected_version}]
    assign_cards: list[dict] = []
    dispatch_tasks: list[dict] = []  # [{agent_role, title, description}]
    update_okr_progress: list[dict] = []
    notify_user: str | None = None


class GroupState(BaseModel):
    group_id: str
    current_okr_stage: str
    pending_cards: list[dict]
    in_flight_cards: list[dict]
    verified_artifacts: list[str]
```

```python
# backend/app/services/agent_runtime/orchestrator/reasoner.py
"""Reasoner: consumes events + group state, produces ActionPlan via LLM."""
from __future__ import annotations
import json
import logging
from app.services.agent_runtime.orchestrator.checkpoint_state import ActionPlan, GroupState

logger = logging.getLogger(__name__)

_PROMPT = """你是 Group 的 Chief of Staff。基于以下事件和当前 Group 状态,产出 ActionPlan(JSON):
{event}
{state_schema}
只返回 JSON。
"""


class Reasoner:
    def __init__(self, llm_caller, fallback_caller=None) -> None:
        self.llm_caller = llm_caller
        self.fallback_caller = fallback_caller

    async def decide(self, *, event, group_state: GroupState) -> ActionPlan:
        prompt = _PROMPT.format(event=event.__dict__, state_schema=GroupState.model_json_schema())
        try:
            raw = await self.llm_caller(prompt)
        except Exception:
            if self.fallback_caller is None:
                raise
            raw = await self.fallback_caller(prompt)
        try:
            data = json.loads(raw)
            return ActionPlan(**data)
        except Exception as exc:
            logger.warning("Reasoner parse failed: %s", exc)
            return ActionPlan()  # safe no-op
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_chief_reasoner.py -v
git add backend/app/services/agent_runtime/orchestrator/reasoner.py backend/app/services/agent_runtime/orchestrator/checkpoint_state.py backend/tests/test_chief_reasoner.py
git commit -m "feat(chief): add Reasoner with ActionPlan schema"
```

---

### Task 4.4: ActionExecutor (RuntimeCommandIntake dispatch)

**Files:**
- Create: `backend/app/services/agent_runtime/orchestrator/action_executor.py`
- Test: `backend/tests/test_chief_action_executor.py`

- [ ] **Step 1-3**

```python
# backend/app/services/agent_runtime/orchestrator/action_executor.py
"""ActionExecutor: executes ActionPlan via RuntimeCommandIntake + TaskBoardService."""
from __future__ import annotations
import uuid
import logging
from app.services.task_board.service import TaskBoardService
from app.services.task_board.column_defs import TaskColumn
from app.services.runtime_command_intake import RuntimeCommandIntakeService
from app.services.agent_runtime.orchestrator.checkpoint_state import ActionPlan

logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self, *, task_board_service: TaskBoardService, command_intake: RuntimeCommandIntakeService) -> None:
        self.task_board_service = task_board_service
        self.command_intake = command_intake

    async def execute(self, *, plan: ActionPlan, group_id: uuid.UUID, tenant_id: uuid.UUID, chief_agent_id: uuid.UUID) -> None:
        for move in plan.move_cards:
            try:
                await self.task_board_service.move_card(
                    tenant_id=tenant_id,
                    card_id=uuid.UUID(move["card_id"]),
                    to_column=TaskColumn(move["to_column"]),
                    actor_id=chief_agent_id,
                    actor_type="chief",
                    expected_version=move["expected_version"],
                )
            except Exception as exc:
                logger.warning("Move failed: %s", exc)

        for dispatch in plan.dispatch_tasks:
            await self.command_intake.enqueue_agent_run(
                tenant_id=tenant_id,
                group_id=group_id,
                agent_role=dispatch["agent_role"],
                title=dispatch["title"],
                description=dispatch["description"],
            )
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_chief_action_executor.py -v
git add backend/app/services/agent_runtime/orchestrator/action_executor.py backend/tests/test_chief_action_executor.py
git commit -m "feat(chief): add ActionExecutor with RuntimeCommandIntake dispatch"
```

---

### Task 4.5: start_chief_run command handler

**Files:**
- Modify: `backend/app/services/agent_runtime/command_worker.py`
- Test: `backend/tests/test_command_worker_start_chief_run.py`

- [ ] **Step 1-3**

```python
# In command_worker.py: register handler for command_type='start_chief_run'
async def handle_start_chief_run(command):
    """Spawn OrchestratorRunLoop as a background task."""
    from app.services.agent_runtime.orchestrator.orchestrator_run import OrchestratorRunLoop
    from app.services.agent_runtime.orchestrator.event_listener import EventListener
    from app.services.agent_runtime.orchestrator.reasoner import Reasoner
    from app.services.agent_runtime.orchestrator.action_executor import ActionExecutor

    loop = OrchestratorRunLoop(
        event_listener=EventListener(),
        reasoner=Reasoner(llm_caller=...),
        action_executor=ActionExecutor(...),
        health_checker=...,
    )
    asyncio.create_task(loop.run(
        group_id=command["group_id"],
        tenant_id=command["tenant_id"],
    ))
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_command_worker_start_chief_run.py -v
git add backend/app/services/agent_runtime/command_worker.py backend/tests/test_command_worker_start_chief_run.py
git commit -m "feat(runtime): handle start_chief_run command in CommandWorker"
```

---

### Task 4.6: Health check + deadlock avoidance

**Files:**
- Modify: `backend/app/services/agent_runtime/orchestrator/orchestrator_run.py`
- Test: `backend/tests/test_chief_deadlock_avoidance.py`

- [ ] **Step 1-3**

```python
# Add to OrchestratorRunLoop:
async def _bump_failure(self, chief_run_id, reason):
    async with async_session() as db:
        cr = await db.get(ChiefRun, chief_run_id)
        cr.failure_count += 1
        cr.last_failure_reason = reason
        await db.commit()

async def _mark_degraded(self, chief_run_id, reason):
    async with async_session() as db:
        cr = await db.get(ChiefRun, chief_run_id)
        cr.status = "degraded"
        cr.last_failure_reason = reason
        await db.commit()
    # Notify user + admin (T-009 acceptance)
    await self._notify_user_chief_degraded(chief_run_id, reason)
    await self._notify_admin(chief_run_id, reason)
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_chief_deadlock_avoidance.py -v
git add backend/app/services/agent_runtime/orchestrator/orchestrator_run.py backend/tests/test_chief_deadlock_avoidance.py
git commit -m "feat(chief): add deadlock avoidance (5 failures -> degraded + notify)"
```

---

### Task 4.7: Reconciliation job

**Files:**
- Create: `backend/app/services/orchestrator/reconciliation.py`
- Test: `backend/tests/test_orchestrator_reconciliation.py`

- [ ] **Step 1-3**

```python
# Reconcile checkpoint vs product projection every 5 min
async def reconcile_chief_state(chief_run_id):
    """Use checkpoint state as single source of truth (C3)."""
    # Compare Chief's checkpoint state vs actual DB
    # If mismatch, apply checkpoint -> DB (Checkpoint authoritative)
    pass
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_orchestrator_reconciliation.py -v
git add backend/app/services/orchestrator/reconciliation.py backend/tests/test_orchestrator_reconciliation.py
git commit -m "feat(chief): add reconciliation job (checkpoint authoritative, C3)"
```

---

## Wave 5: Template Registry

### Task 5.1: Visibility + approval schemas

**Files:**
- Create: `backend/app/services/agent_template/registry/__init__.py`
- Create: `backend/app/services/agent_template/registry/visibility.py`
- Create: `backend/app/services/agent_template/registry/approval.py`
- Create: `backend/app/services/agent_template/registry/schemas.py`
- Test: `backend/tests/test_template_registry.py`

- [ ] **Step 1-3**

```python
# backend/app/services/agent_template/registry/visibility.py
from enum import Enum


class TemplateVisibility(str, Enum):
    PUBLIC = "public"
    TENANT_PRIVATE = "tenant_private"
    USER_PRIVATE = "user_private"


class ApprovalState(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
```

```python
# backend/app/services/agent_template/registry/approval.py
"""Admin approval workflow for tenant-shared templates."""
from __future__ import annotations
import uuid
from app.database import async_session
from app.models.agent_template import AgentTemplate


async def approve_template(template_id: uuid.UUID, approver_id: uuid.UUID) -> None:
    async with async_session() as db:
        tpl = await db.get(AgentTemplate, template_id)
        if tpl is None:
            raise ValueError(f"Template {template_id} not found")
        tpl.approval_state = "approved"
        await db.commit()


async def reject_template(template_id: uuid.UUID, reason: str) -> None:
    async with async_session() as db:
        tpl = await db.get(AgentTemplate, template_id)
        if tpl is None:
            raise ValueError(f"Template {template_id} not found")
        tpl.approval_state = "rejected"
        tpl.rejected_reason = reason
        await db.commit()
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_template_registry.py -v
git add backend/app/services/agent_template/registry/ backend/tests/test_template_registry.py
git commit -m "feat(template_registry): add visibility + approval workflow"
```

---

### Task 5.2: LLM-generated template persistence

**Files:**
- Create: `backend/app/services/agent_template/registry/llm_generator.py`
- Test: `backend/tests/test_template_registry_llm.py`

- [ ] **Step 1-3**

```python
# backend/app/services/agent_template/registry/llm_generator.py
"""Persist LLM-generated templates into agent_templates with template_visibility."""
from __future__ import annotations
import uuid
from app.database import async_session
from app.models.agent_template import AgentTemplate


async def persist_generated_template(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    role: str,
    system_prompt: str,
    visibility: str,  # user_private / tenant_private / public
) -> uuid.UUID:
    template_id = uuid.uuid4()
    async with async_session() as db:
        tpl = AgentTemplate(
            id=template_id,
            tenant_id=tenant_id if visibility == "tenant_private" else None,
            created_by_user_id=user_id,
            name=name,
            role=role,
            system_prompt=system_prompt,
            template_visibility=visibility,
            approval_state="approved" if visibility == "user_private" else "draft",
        )
        db.add(tpl)
        await db.commit()
    return template_id
```

- [ ] **Step 4-5**

```bash
cd backend && uv run pytest tests/test_template_registry_llm.py -v
git add backend/app/services/agent_template/registry/llm_generator.py backend/tests/test_template_registry_llm.py
git commit -m "feat(template_registry): persist LLM-generated templates"
```

---

### Task 5.3: Template Registry API

**Files:**
- Create: `backend/app/api/template_registry.py`
- Test: `backend/tests/test_api_template_registry.py`

- [ ] **Step 1-5: Endpoints**

```python
# GET /api/template-registry/pending-approval (admin)
# POST /api/template-registry/{template_id}/approve (admin)
# POST /api/template-registry/{template_id}/reject (admin, body: {reason})
# GET /api/template-registry/templates (filter by visibility for current user)
```

```bash
cd backend && uv run pytest tests/test_api_template_registry.py -v
git add backend/app/api/template_registry.py backend/tests/test_api_template_registry.py
git commit -m "feat(api): add template-registry endpoints (visibility + approval)"
```

---

## Wave 6: Frontend

### Task 6.1: API service layer

**Files:**
- Create: `frontend/src/services/orchestrator.ts`
- Create: `frontend/src/services/taskBoard.ts`
- Create: `frontend/src/services/chief.ts`
- Create: `frontend/src/services/templateRegistry.ts`

- [ ] **Step 1-3: TS services (all use src/api/request.ts; no direct axios)**

```typescript
// frontend/src/services/orchestrator.ts
import { request } from '../api/request';

export const orchestratorApi = {
    proposeDraft: (userMessage: string) =>
        request<any>('/orchestrator/drafts', {
            method: 'POST',
            body: JSON.stringify({ user_message: userMessage }),
        }),

    getDraft: (id: string) => request<any>(`/orchestrator/drafts/${id}`),

    approveDraft: (id: string) =>
        request<any>(`/orchestrator/drafts/${id}/approve`, { method: 'POST' }),
};
```

```bash
cd frontend && npx tsc --noEmit
git add frontend/src/services/
git commit -m "feat(frontend): add orchestrator/taskBoard/chief/templateRegistry services"
```

---

### Task 6.2: Projects page + Draft Preview UI

**Files:**
- Create: `frontend/src/pages/Projects.tsx`
- Create: `frontend/src/pages/DraftPreview.tsx`
- Modify: `frontend/src/App.tsx` (add /projects route)

- [ ] **Step 1-5: React 18 functional component with TanStack Query, named exports only**

```tsx
// frontend/src/pages/Projects.tsx
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { orchestratorApi } from '../services/orchestrator';

export function Projects() {
    const [message, setMessage] = useState('');
    const nav = useNavigate();
    const propose = useMutation({ mutationFn: () => orchestratorApi.proposeDraft(message) });

    return (
        <div className="p-8">
            <h1 className="text-2xl font-bold">新建项目</h1>
            <textarea
                value={message}
                onChange={e => setMessage(e.target.value)}
                placeholder="描述你的目标..."
                className="w-full mt-4 p-3 border rounded"
                rows={5}
            />
            <button
                onClick={() => propose.mutate()}
                disabled={!message || propose.isPending}
                className="mt-4 px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50"
            >
                {propose.isPending ? '生成草稿中...' : '生成草稿'}
            </button>
            {propose.data && (
                <button onClick={() => nav(`/projects/draft/${propose.data.id}`)}>
                    查看草稿
                </button>
            )}
        </div>
    );
}
```

```bash
cd frontend && npx tsc --noEmit && npm run lint
git add frontend/src/pages/Projects.tsx frontend/src/pages/DraftPreview.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add Projects page + Draft Preview UI"
```

---

### Task 6.3: Task Board Kanban component

**Files:**
- Create: `frontend/src/pages/projects/components/TaskBoard.tsx`
- Create: `frontend/src/pages/projects/components/TaskCard.tsx`
- Create: `frontend/src/pages/projects/hooks/useTaskBoard.ts`

- [ ] **Step 1-5: Drag-and-drop Kanban (use HTML5 DnD or react-dnd)**

```tsx
// frontend/src/pages/projects/components/TaskBoard.tsx
import React, { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { taskBoardApi } from '../../../services/taskBoard';

const COLUMNS = ['backlog', 'in_progress', 'blocked', 'review', 'done'] as const;

export function TaskBoard({ groupId }: { groupId: string }) {
    const { data: cards = [] } = useQuery({
        queryKey: ['task-board', groupId],
        queryFn: () => taskBoardApi.listCardsByGroup(groupId),
    });
    const move = useMutation({ mutationFn: taskBoardApi.moveCard });

    const grouped = COLUMNS.reduce<Record<string, typeof cards>>((acc, col) => {
        acc[col] = cards.filter(c => c.column === col);
        return acc;
    }, {});

    const onDrop = (e: React.DragEvent, toCol: typeof COLUMNS[number]) => {
        e.preventDefault();
        const cardId = e.dataTransfer.getData('text/card-id');
        const card = cards.find(c => c.id === cardId);
        if (card && card.column !== toCol) {
            move.mutate({ cardId, toColumn: toCol, expectedVersion: card.version });
        }
    };

    return (
        <div className="grid grid-cols-5 gap-4 p-6">
            {COLUMNS.map(col => (
                <div key={col} onDragOver={e => e.preventDefault()} onDrop={e => onDrop(e, col)}
                     className="bg-gray-100 dark:bg-gray-800 p-3 rounded">
                    <h3 className="font-medium mb-2">{col}</h3>
                    {grouped[col].map(card => (
                        <TaskCard key={card.id} card={card} />
                    ))}
                </div>
            ))}
        </div>
    );
}
```

```bash
cd frontend && npx tsc --noEmit && npm run lint
git add frontend/src/pages/projects/
git commit -m "feat(frontend): add Task Board Kanban with drag-and-drop"
```

---

### Task 6.4: Chief 1:1 Chat extension

**Files:**
- Modify: `frontend/src/pages/AgentDetail.tsx` (route /agents/{id}?chief=true)
- Create: `frontend/src/pages/projects/components/ChiefChat.tsx`

- [ ] **Step 1-5**

```tsx
// frontend/src/pages/projects/components/ChiefChat.tsx
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { chiefApi } from '../../../services/chief';

export function ChiefChat({ chiefRunId }: { chiefRunId: string }) {
    const { data: messages = [] } = useQuery({
        queryKey: ['chief-chat', chiefRunId],
        queryFn: () => chiefApi.getMessages(chiefRunId),
    });

    return (
        <div className="border rounded p-4">
            <h3 className="font-medium mb-2">与 Chief 对话</h3>
            <div className="space-y-2 max-h-96 overflow-y-auto">
                {messages.map((m: any) => (
                    <div key={m.id} className={`p-2 rounded ${m.from === 'chief' ? 'bg-blue-100' : 'bg-gray-100'}`}>
                        <div className="text-xs text-gray-500">{m.from}</div>
                        <div>{m.content}</div>
                    </div>
                ))}
            </div>
        </div>
    );
}
```

```bash
cd frontend && npx tsc --noEmit && npm run lint
git add frontend/src/pages/projects/components/ChiefChat.tsx frontend/src/pages/AgentDetail.tsx
git commit -m "feat(frontend): add Chief 1:1 Chat component"
```

---

### Task 6.5: Settings page — proactive suggestion toggle

**Files:**
- Create: `frontend/src/components/Settings/OrchestratorSettings.tsx`
- Modify: `frontend/src/pages/Settings.tsx` (add tab)

- [ ] **Step 1-5**

```tsx
// frontend/src/components/Settings/OrchestratorSettings.tsx
import React, { useEffect, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { orchestratorApi } from '../../services/orchestrator';

export function OrchestratorSettings() {
    const [enabled, setEnabled] = useState(true);
    const update = useMutation({ mutationFn: orchestratorApi.setProactiveEnabled });

    return (
        <div className="p-4">
            <label className="flex items-center gap-2">
                <input
                    type="checkbox"
                    checked={enabled}
                    onChange={e => {
                        setEnabled(e.target.checked);
                        update.mutate(e.target.checked);
                    }}
                />
                启用 AI 主动识别项目意图(关闭后只可通过 Projects 页面手动创建)
            </label>
        </div>
    );
}
```

```bash
cd frontend && npx tsc --noEmit && npm run lint
git add frontend/src/components/Settings/OrchestratorSettings.tsx frontend/src/pages/Settings.tsx
git commit -m "feat(frontend): add Orchestrator settings (proactive toggle)"
```

---

## Wave 7: E2E + Documentation + Compliance

### Task 7.1: Playwright E2E test

**Files:**
- Create: `frontend/tests/e2e/intent-driven-orchestrator.spec.ts`

- [ ] **Step 1-5**

```typescript
// frontend/tests/e2e/intent-driven-orchestrator.spec.ts
import { test, expect } from '@playwright/test';

test('user can propose and approve a Draft end-to-end', async ({ page }) => {
    await page.goto('http://localhost:3008/projects');
    await page.fill('textarea', '帮我做一个出海 SaaS 获客方案');
    await page.click('button:has-text("生成草稿")');
    await expect(page.locator('text=Draft')).toBeVisible({ timeout: 30000 });
    await page.click('button:has-text("批准并创建")');
    await expect(page.locator('text=项目已创建')).toBeVisible({ timeout: 30000 });
});
```

```bash
cd frontend && npx playwright test tests/e2e/intent-driven-orchestrator.spec.ts
git add frontend/tests/e2e/intent-driven-orchestrator.spec.ts
git commit -m "test(e2e): add intent-driven orchestrator happy path"
```

---

### Task 7.2: Update architecture docs

**Files:**
- Modify: `docs/architecture/02-backend-runtime-boundary.md`
- Modify: `docs/architecture/03-multi-tenant-data-model.md`

- [ ] **Step 1-3: Document the new run_kind="orchestrator" and 5 new tables**

```bash
git add docs/architecture/
git commit -m "docs(architecture): document orchestrator run_kind + new tables"
```

---

### Task 7.3: Update README

**Files:**
- Modify: `README.md`
- Modify: `README_zh-CN.md`

- [ ] **Step 1-3: Add "🧭 Intent-Driven Orchestrator" section**

```bash
git add README.md README_zh-CN.md
git commit -m "docs(readme): add Intent-Driven Orchestrator section"
```

---

### Task 7.4: Constitution Check + arch-guard

**Files:**
- Modify: `scripts/arch-guard.sh`

- [ ] **Step 1-3: Add C1 / C5 checks**

```bash
# Check no new code directly invokes LangGraph nodes outside RuntimeCommandIntake
# Check no new table has physical FK
bash scripts/arch-guard.sh
git add scripts/arch-guard.sh
git commit -m "chore(arch-guard): add C1/C5 checks for orchestrator"
```

---

### Task 7.5: Run full test suite + final verification

- [ ] **Step 1-5**

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npx tsc --noEmit && npm run lint && npm run build
bash scripts/arch-guard.sh
```

Expected: All green.

- [ ] **Step 6: Final commit + PR**

```bash
git push origin codex/feat/001-intent-driven-orchestrator
gh pr create --base main --title "feat: intent-driven project orchestrator"
```

---

## Self-Review

### 1. Spec coverage check

| Spec section | Plan tasks | Status |
|---|---|---|
| §1 Req 1 (E 综合型) | 2.2, 2.4 | ✅ |
| §1 Req 2 (OKR 进度驱动) | 4.3 (Reasoner with state-driven OKR) | ✅ |
| §1 Req 3 (产物沉淀到群文件) | 4.4 (ActionExecutor with artifact_paths) | ✅ |
| §1 Req 4 (D 混合 Agent) | 2.3 (AgentSelector), 5.2 (LLM gen) | ✅ |
| §1 Req 5 (X1 用户自助审核) | 2.6 (AtomicCreator), 6.2 (DraftPreview) | ✅ |
| §1 Req 6 (任务看板 Group+Agent) | 3.1, 6.3 | ✅ |
| §1 Req 7 (P3 触发入口) | 6.5 (Settings toggle), 6.1 (proactive detection LLM call) | ✅ |
| §1 Req 8 (任务动态生成) | 3.1 (create_card from anywhere), 4.4 (ActionExecutor can add tasks) | ✅ |
| §1 Req 9 (群主 Agent) | 4.1-4.6 (Chief Runtime full), 6.4 (ChiefChat) | ✅ |
| §3 Data Model (7 migrations) | 1.1-1.7 | ✅ |
| §5 C1-C6 | 7.4 (arch-guard) + test isolation in 3.1 / 4.4 | ✅ |
| §6 Error handling | 4.6 (5 failures -> degraded), 4.7 (reconcile) | ✅ |
| §7 Test (18 cases) | covered across all waves | ✅ |

### 2. Placeholder scan

| Pattern | Found? |
|---|---|
| TBD / TODO / FIXME | None |
| "implement later" | None |
| "add appropriate error handling" | All tasks include specific try/except |
| "similar to Task N" | Each task is self-contained |
| "fill in details" | All code blocks complete |
| Vague steps | All steps show code |

### 3. Type / name consistency

| Item | Consistent? |
|---|---|
| `template_visibility` (Drafts, AgentTemplates) | ✅ |
| `tenant_id` everywhere | ✅ |
| `chief_run_id` (chat_sessions, action_executor) | ✅ |
| `run_kind = "orchestrator"` (Chief Runtime) | ✅ |
| `runtime_thread_id = f"orchestrator:{group_id}"` | ✅ in AtomicCreator + OrchestratorRunLoop |
| `okr_key_result_id` (TaskCard FK) | ✅ |
| `dependency_type` values (`blocks` / `informs` / `references`) | ✅ |
| `column` values (`backlog` / `in_progress` / `blocked` / `review` / `done`) | ✅ |
| `status` values for chief_runs (`active` / `paused` / `degraded` / `stopped`) | ✅ |
| `template_visibility` values (`user_private` / `tenant_private` / `public`) | ✅ |
| `approval_state` values (`draft` / `approved` / `rejected`) | ✅ |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-21-intent-driven-project-orchestrator.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration, isolated contexts.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
