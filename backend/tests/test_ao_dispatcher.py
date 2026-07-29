"""P2.1 tests — ``ao.dispatcher`` round-trip coverage.

Four scenarios required by the task spec:

1. ``dispatch_task_to_role`` persists a ``WorkflowRunStep`` row and
   enqueues a group message when the call site omits ``step_id``.
2. ``run_dispatch_loop`` honours DAG dependencies: a pending step whose
   ``depends_on`` references an unfinished predecessor is held back,
   while a step whose deps are already ``succeeded`` is dispatched and
   stamped as ``running``.
3. ``collect_step_result`` writes the output excerpt, output file, and
   token counts onto the step row, then transitions ``status`` to
   ``succeeded`` when no downstream quality step exists.
4. Boundary cases: a missing step id raises ``AOIntegrationError``;
   ``output_file=None`` is allowed.

The tests use lightweight ``SimpleNamespace`` stubs for the dispatch
scope / step rows so they don't need the full SQLAlchemy schema. They
keep the dispatcher module honest by monkeypatching the same
``dispatch_task_to_role`` and ``get_run_steps`` / ``get_step`` /
``has_quality_step`` boundaries the production code uses.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.ao import dispatcher, scheduler_tools
from app.services.ao.dispatcher import (
    collect_step_result,
    run_dispatch_loop,
)
from app.services.ao.run_repository import mark_step_status

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeDB:
    """AsyncSession stub that records ``add()`` / ``flush()`` calls.

    ``scalar`` / ``execute`` return whatever the test sets via
    ``scalar_values`` / ``execute_values`` so the dispatcher stays
    decoupled from real SQLAlchemy queries in this unit test.
    """

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushed = 0
        self.scalar_values: list[Any] = []
        self.execute_values: list[Any] = []

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed += 1

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def scalar(self, _statement: Any) -> Any:
        if not self.scalar_values:
            return None
        return self.scalar_values.pop(0)

    async def execute(self, _statement: Any) -> Any:
        class _Result:
            def __init__(self, scalar_value: Any) -> None:
                self._scalar = scalar_value

            def scalar_one_or_none(self) -> Any:
                return self._scalar

            def scalars(self) -> Any:
                return SimpleNamespace(all=list)

        if not self.execute_values:
            return _Result(None)
        return _Result(self.execute_values.pop(0))

    async def get(self, model: Any, key: Any) -> Any:
        # Tests inject the row they want via the ``_registry`` mapping.
        return self._registry.get((model, key)) if hasattr(self, "_registry") else None


@pytest.fixture
def ao_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point the scheduler tools at tmp paths so init_workflow_dir doesn't pollute the repo."""
    workflows_dir = tmp_path / "workflows"
    output_dir = tmp_path / "ao-output"
    workflows_dir.mkdir()
    settings = SimpleNamespace(AO_WORKFLOWS_DIR=str(workflows_dir), AO_OUTPUT_DIR=str(output_dir))
    monkeypatch.setattr(scheduler_tools, "get_settings", lambda: settings)
    return workflows_dir, output_dir


def _make_dispatch_scope(
    *,
    workflow_id: uuid.UUID,
    target_agent_id: uuid.UUID,
    scheduler_agent_id: uuid.UUID,
) -> SimpleNamespace:
    """Build a ``_DispatchScope``-shaped stub the dispatcher can consume."""
    return SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workflow_id=workflow_id,
        group_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        scheduler_agent_id=scheduler_agent_id,
        creator_id=uuid.uuid4(),
        sender_participant_id=uuid.uuid4(),
        target_participant_id=uuid.uuid4(),
    )


def _make_step(
    *,
    workflow_id: uuid.UUID,
    step_key: str,
    order: int,
    agent_id: uuid.UUID | None,
    depends_on: list[str],
    status: str = "pending",
) -> SimpleNamespace:
    """Return a ``WorkflowRunStep``-shaped stub with deterministic fields."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key=step_key,
        step_order=order,
        role_path=f"product/{step_key}",
        agent_id=agent_id,
        task_summary=f"do {step_key}",
        input_refs={"topic": step_key},
        output_var=f"{step_key}_out",
        depends_on=depends_on,
        status=status,
        acceptance_text=f'["{step_key}_artifact"]',
        output_excerpt=None,
        output_file=None,
        input_tokens=None,
        output_tokens=None,
    )


# ---------------------------------------------------------------------------
# 1. dispatch_task_to_role inserts a step row when step_id is omitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_task_to_role_inserts_step_row_and_enqueues_message(
    monkeypatch: pytest.MonkeyPatch,
    ao_paths: tuple[Path, Path],
) -> None:
    from app.models.workflow_run import WorkflowRunStep

    workflow_id = uuid.uuid4()
    scheduler_agent_id = uuid.uuid4()
    target_agent_id = uuid.uuid4()
    db = _FakeDB()
    scope = _make_dispatch_scope(
        workflow_id=workflow_id,
        target_agent_id=target_agent_id,
        scheduler_agent_id=scheduler_agent_id,
    )
    monkeypatch.setattr(scheduler_tools, "_load_dispatch_scope", lambda *a, **kw: scope)

    calls: list[dict] = []
    sent_message_id = uuid.uuid4()

    async def fake_enqueue(_db: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(id=sent_message_id),
            dispatch_kind="single",
        )

    monkeypatch.setattr(scheduler_tools.group_message_service, "enqueue_group_message", fake_enqueue)

    with scheduler_tools.scheduler_tool_context(
        db=db,
        workflow_id=workflow_id,
        actor_agent_id=scheduler_agent_id,
        user_id=scope.creator_id,
    ):
        result = await scheduler_tools.dispatch_task_to_role(
            str(target_agent_id),
            "整理需求基线",
            {"source": "brief.md"},
            expected_outputs=["requirements.md", "tasks.json"],
        )

    step_rows = [item for item in db.added if isinstance(item, WorkflowRunStep)]
    assert len(step_rows) == 1, "dispatch_task_to_role must insert a WorkflowRunStep row"
    inserted = step_rows[0]
    assert inserted.workflow_id == workflow_id
    assert inserted.agent_id == target_agent_id
    assert inserted.status == "pending"
    assert inserted.task_summary == "整理需求基线"
    assert inserted.input_refs == {"source": "brief.md"}
    assert inserted.acceptance_text is not None
    assert "requirements.md" in inserted.acceptance_text

    assert result["ok"] is True
    assert result["step_id"] == str(inserted.id)
    assert result["task_id"]  # Task row also created
    assert calls, "group_message_service.enqueue_group_message must be called"
    sent = calls[0]
    assert sent["mention_participant_ids"] == [scope.target_participant_id]
    assert sent["project_task_dispatch"] is False
    assert "整理需求基线" in sent["content"]
    assert str(inserted.id) in sent["content"]
    assert "requirements.md" in sent["content"]
    # init_workflow_dir side effect: run_dir is returned and the directory exists.
    assert result["run_dir"].endswith(str(workflow_id))
    assert Path(result["run_dir"]).is_dir()


# ---------------------------------------------------------------------------
# 2. run_dispatch_loop respects DAG dependencies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dispatch_loop_dispatches_only_ready_steps(
    monkeypatch: pytest.MonkeyPatch,
    ao_paths: tuple[Path, Path],
) -> None:
    workflow_id = uuid.uuid4()
    scheduler_agent_id = uuid.uuid4()
    clarify_agent = uuid.uuid4()
    execute_agent = uuid.uuid4()
    review_agent = uuid.uuid4()

    clarify = _make_step(
        workflow_id=workflow_id,
        step_key="clarify",
        order=0,
        agent_id=clarify_agent,
        depends_on=[],
        status="pending",
    )
    execute = _make_step(
        workflow_id=workflow_id,
        step_key="execute",
        order=1,
        agent_id=execute_agent,
        depends_on=["clarify"],
        status="pending",
    )
    review = _make_step(
        workflow_id=workflow_id,
        step_key="review",
        order=2,
        agent_id=review_agent,
        depends_on=["execute"],
        status="pending",
    )
    steps = [clarify, execute, review]

    # Monkeypatch the repository boundary so we never hit SQLAlchemy.
    async def fake_get_run_steps(_db, *, workflow_id):
        assert workflow_id is not None
        return list(steps)

    async def fake_has_quality_step(_db, *, workflow_id, step_key):
        return False

    monkeypatch.setattr(dispatcher, "get_run_steps", fake_get_run_steps)
    monkeypatch.setattr(dispatcher, "has_quality_step", fake_has_quality_step)

    # Track every dispatch_task_to_role invocation so the dependency rule
    # is observed.  The fake mutates the step's status + started_at so
    # downstream iterations see a satisfied dependency.
    dispatched: list[str] = []

    async def fake_dispatch(role_agent_id, task_summary, inputs=None, **kwargs):
        dispatched.append(kwargs.get("step_id") or "")
        step_id = uuid.UUID(kwargs["step_id"])
        for step in steps:
            if step.id == step_id:
                step.status = "running"
                step.started_at = "sentinel"
        return {"ok": True, "task_id": "t", "step_id": str(step_id)}

    monkeypatch.setattr(dispatcher, "_scheduler_dispatch_task_to_role", fake_dispatch)

    async def fake_mark_step_status(*args, **kwargs):
        return None

    monkeypatch.setattr(dispatcher, "mark_step_status", fake_mark_step_status)

    db = _FakeDB()
    result = await run_dispatch_loop(
        db,
        workflow_id=workflow_id,
        scheduler_agent_id=scheduler_agent_id,
        creator_id=uuid.uuid4(),
    )

    # Round 1: only clarify is ready.
    # Round 2: clarify must complete before execute / review run, so we
    # expect a single iteration to fire (clarify).  The function only
    # runs once because we don't loop internally.
    assert result["dispatched_count"] == 1
    assert result["step_ids"] == [str(clarify.id)]
    assert dispatched == [str(clarify.id)]


@pytest.mark.asyncio
async def test_run_dispatch_loop_dispatches_ready_step_when_dependencies_satisfied(
    monkeypatch: pytest.MonkeyPatch,
    ao_paths: tuple[Path, Path],
) -> None:
    """Once ``clarify`` has succeeded, ``execute`` should fire on the next pass."""
    workflow_id = uuid.uuid4()
    scheduler_agent_id = uuid.uuid4()
    clarify_agent = uuid.uuid4()
    execute_agent = uuid.uuid4()
    review_agent = uuid.uuid4()

    clarify = _make_step(
        workflow_id=workflow_id,
        step_key="clarify",
        order=0,
        agent_id=clarify_agent,
        depends_on=[],
        status="succeeded",
    )
    execute = _make_step(
        workflow_id=workflow_id,
        step_key="execute",
        order=1,
        agent_id=execute_agent,
        depends_on=["clarify"],
        status="pending",
    )
    review = _make_step(
        workflow_id=workflow_id,
        step_key="review",
        order=2,
        agent_id=review_agent,
        depends_on=["execute"],
        status="pending",
    )
    steps = [clarify, execute, review]

    async def fake_get_run_steps(_db, *, workflow_id):
        return list(steps)

    monkeypatch.setattr(dispatcher, "get_run_steps", fake_get_run_steps)

    async def fake_has_quality_step(_db, *, workflow_id, step_key):
        return False

    monkeypatch.setattr(dispatcher, "has_quality_step", fake_has_quality_step)

    dispatched_ids: list[str] = []

    async def fake_dispatch(role_agent_id, task_summary, inputs=None, **kwargs):
        dispatched_ids.append(kwargs.get("step_id") or "")
        return {"ok": True, "task_id": "t", "step_id": kwargs["step_id"]}

    monkeypatch.setattr(dispatcher, "_scheduler_dispatch_task_to_role", fake_dispatch)

    async def fake_mark_step_status(*args, **kwargs):
        return None

    monkeypatch.setattr(dispatcher, "mark_step_status", fake_mark_step_status)

    result = await run_dispatch_loop(
        _FakeDB(),
        workflow_id=workflow_id,
        scheduler_agent_id=scheduler_agent_id,
        creator_id=uuid.uuid4(),
    )

    assert result["dispatched_count"] == 1
    assert dispatched_ids == [str(execute.id)]
    assert result["step_ids"] == [str(execute.id)]


@pytest.mark.asyncio
async def test_run_dispatch_loop_skips_steps_with_no_agent(
    monkeypatch: pytest.MonkeyPatch,
    ao_paths: tuple[Path, Path],
) -> None:
    """Pending step with ``agent_id=None`` raises ``AOIntegrationError``."""
    workflow_id = uuid.uuid4()
    step = _make_step(
        workflow_id=workflow_id,
        step_key="orphan",
        order=0,
        agent_id=None,
        depends_on=[],
    )

    async def fake_get_run_steps_orphan(_db, *, workflow_id):
        return [step]

    monkeypatch.setattr(dispatcher, "get_run_steps", fake_get_run_steps_orphan)
    monkeypatch.setattr(dispatcher, "has_quality_step", lambda *a, **kw: False)

    with pytest.raises(scheduler_tools.AOIntegrationError, match="agent_id"):
        await run_dispatch_loop(_FakeDB(), workflow_id=workflow_id)


# ---------------------------------------------------------------------------
# 3. collect_step_result writes output excerpt / tokens and transitions status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_step_result_persists_output_and_marks_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid.uuid4()
    step = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="execute",
        output_excerpt=None,
        output_file=None,
        input_tokens=None,
        output_tokens=None,
        status="running",
    )

    async def fake_get_step(_db, *, step_id):
        assert step_id == step.id
        return step

    monkeypatch.setattr(dispatcher, "get_step", fake_get_step)

    async def fake_has_quality_step_no_quality(_db, *, workflow_id, step_key):
        return False

    monkeypatch.setattr(dispatcher, "has_quality_step", fake_has_quality_step_no_quality)

    db = _FakeDB()
    result = await collect_step_result(
        db,
        step_id=step.id,
        output_excerpt="executor finished the artifact",
        output_file=None,
        input_tokens=120,
        output_tokens=80,
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["output_excerpt"] == "executor finished the artifact"
    assert result["output_file"] is None
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 80
    assert result["completed_at"]
    assert step.output_excerpt == "executor finished the artifact"
    assert step.input_tokens == 120
    assert step.output_tokens == 80
    assert step.status == "succeeded"
    assert db.flushed >= 1


@pytest.mark.asyncio
async def test_collect_step_result_marks_quality_checking_when_quality_step_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid.uuid4()
    step = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="execute",
        output_excerpt=None,
        output_file=None,
        input_tokens=None,
        output_tokens=None,
        status="running",
    )

    async def fake_get_step(_db, *, step_id):
        return step

    async def fake_has_quality_step(_db, *, workflow_id, step_key):
        assert workflow_id is not None
        assert step_key == "execute"
        return True

    monkeypatch.setattr(dispatcher, "get_step", fake_get_step)
    monkeypatch.setattr(dispatcher, "has_quality_step", fake_has_quality_step)

    result = await collect_step_result(
        _FakeDB(),
        step_id=step.id,
        output_excerpt="artifact ready",
        output_file="/tmp/run/01-步骤输出/execute.md",
        input_tokens=200,
        output_tokens=140,
    )

    assert result["status"] == "quality_checking"
    assert step.status == "quality_checking"
    assert step.output_file == "/tmp/run/01-步骤输出/execute.md"


# ---------------------------------------------------------------------------
# 4. Boundary cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_step_result_raises_when_step_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_step(_db, *, step_id):
        return None

    monkeypatch.setattr(dispatcher, "get_step", fake_get_step)

    with pytest.raises(scheduler_tools.AOIntegrationError, match="not found"):
        await collect_step_result(
            _FakeDB(),
            step_id=uuid.uuid4(),
            output_excerpt="orphan",
            output_file=None,
            input_tokens=None,
            output_tokens=None,
        )


@pytest.mark.asyncio
async def test_collect_step_result_accepts_missing_output_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-memory excerpt with no file on disk must still be persisted."""
    workflow_id = uuid.uuid4()
    step = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="execute",
        output_excerpt=None,
        output_file=None,
        input_tokens=None,
        output_tokens=None,
        status="running",
    )

    async def fake_get_step(_db, *, step_id):
        return step

    monkeypatch.setattr(dispatcher, "get_step", fake_get_step)

    async def fake_has_quality_step_inline(_db, *, workflow_id, step_key):
        return False

    monkeypatch.setattr(dispatcher, "has_quality_step", fake_has_quality_step_inline)

    result = await collect_step_result(
        _FakeDB(),
        step_id=step.id,
        output_excerpt="executor returned the artifact inline only",
        output_file=None,
        input_tokens=None,
        output_tokens=None,
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert step.output_file is None
    assert step.input_tokens is None
    assert step.output_tokens is None


@pytest.mark.asyncio
async def test_dispatch_task_to_role_updates_existing_step(
    monkeypatch: pytest.MonkeyPatch,
    ao_paths: tuple[Path, Path],
) -> None:
    """When ``step_id`` is given, the existing row is updated, not duplicated."""
    from app.models.workflow_run import WorkflowRunStep

    workflow_id = uuid.uuid4()
    scheduler_agent_id = uuid.uuid4()
    target_agent_id = uuid.uuid4()
    existing_step_id = uuid.uuid4()
    existing_step = WorkflowRunStep(
        id=existing_step_id,
        tenant_id=uuid.uuid4(),
        workflow_id=workflow_id,
        step_key="execute",
        step_order=1,
        role_path="product/executor-0",
        agent_id=target_agent_id,
        task_summary="execute plan",
        input_refs={"prior": "clarify"},
        depends_on=["clarify"],
        status="pending",
    )

    db = _FakeDB()
    db._registry = {(WorkflowRunStep, existing_step_id): existing_step}
    scope = _make_dispatch_scope(
        workflow_id=workflow_id,
        target_agent_id=target_agent_id,
        scheduler_agent_id=scheduler_agent_id,
    )
    monkeypatch.setattr(scheduler_tools, "_load_dispatch_scope", lambda *a, **kw: scope)

    async def fake_enqueue(_db, **kwargs):
        return SimpleNamespace(
            message=SimpleNamespace(id=uuid.uuid4()),
            dispatch_kind="single",
        )

    monkeypatch.setattr(scheduler_tools.group_message_service, "enqueue_group_message", fake_enqueue)

    with scheduler_tools.scheduler_tool_context(
        db=db,
        workflow_id=workflow_id,
        actor_agent_id=scheduler_agent_id,
        user_id=scope.creator_id,
    ):
        result = await scheduler_tools.dispatch_task_to_role(
            str(target_agent_id),
            "execute plan v2",
            {"override": "execute plan"},
            expected_outputs=["artifact.md"],
            step_id=str(existing_step_id),
        )

    new_step_rows = [item for item in db.added if isinstance(item, WorkflowRunStep)]
    assert new_step_rows == [], "When step_id is supplied, no new WorkflowRunStep should be inserted"
    assert result["step_id"] == str(existing_step_id)
    assert result["step_key"] == "execute"
    assert existing_step.status == "running"
    assert existing_step.started_at is not None
    assert existing_step.agent_id == target_agent_id
    assert existing_step.task_summary == "execute plan v2"
    assert existing_step.input_refs == {"override": "execute plan"}
    assert "artifact.md" in (existing_step.acceptance_text or "")


# ---------------------------------------------------------------------------
# 5. End-to-end: run_repository.mark_step_status round-trip against SQLite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_step_status_round_trip_via_sqlite() -> None:
    """``mark_step_status`` should be the canonical repo-level status setter."""
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB

    from app.database import Base
    from app.models.project import ProjectWorkflow
    from app.models.workflow_run import WorkflowRunStep

    jsonb_swaps: dict[Any, Any] = {}
    server_default_swaps: dict[Any, Any] = {}
    for table in (ProjectWorkflow.__table__, WorkflowRunStep.__table__):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_swaps[column] = column.type
                column.type = JSON()
            if column.server_default is not None and "::jsonb" in str(column.server_default.arg):
                server_default_swaps[column] = column.server_default
                column.server_default = None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[ProjectWorkflow.__table__, WorkflowRunStep.__table__],
            )
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as session:  # type: AsyncSession
            workflow = ProjectWorkflow(
                id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                creator_id=uuid.uuid4(),
                name="AO Loop",
                template_key="hr_generated",
                requirements="Build the loop.",
                status="active",
                team_plan={"roles": []},
            )
            session.add(workflow)
            await session.flush()

            step = WorkflowRunStep(
                id=uuid.uuid4(),
                tenant_id=workflow.tenant_id,
                workflow_id=workflow.id,
                step_key="clarify",
                step_order=0,
                role_path="product/project-scheduler",
                agent_id=uuid.uuid4(),
                task_summary="把需求拆成 3~5 步",
                output_var="plan",
                depends_on=[],
                acceptance_text="包含执行计划",
                status="pending",
            )
            session.add(step)
            await session.flush()

            from datetime import UTC, datetime

            now = datetime.now(UTC)
            refreshed = await mark_step_status(
                session,
                step_id=step.id,
                status="running",
                started_at=now,
            )
            assert refreshed is not None
            assert refreshed.status == "running"
            assert refreshed.started_at == now
    finally:
        for column, original in jsonb_swaps.items():
            column.type = original
        for column, original in server_default_swaps.items():
            column.server_default = original
        await engine.dispose()
