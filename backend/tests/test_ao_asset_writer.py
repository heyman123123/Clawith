"""P2.3 tests — workflow asset writer + scanner + light hooks.

Five coverage areas required by the task spec:

1. ``write_step_asset`` writes the file to disk *and* stages a
   ``WorkflowStepAsset`` row carrying sha256 + byte size.
2. ``write_readme`` overwrites the stage README deterministically.
3. ``sync_workflow_assets`` discovers new files, inserts rows for them
   and flags missing rows as ``orphaned`` in metadata.
4. The four stage directories (00..03) actually exist after init and
   are visible to the scan.
5. The light hooks (``quality_engine.run_quality_check`` +
   ``scheduler_tools.dispatch_task_to_role`` README refresh) stage
   the right asset on the right side-effect.

All tests run against a real SQLite in-memory schema built via
``Base.metadata.create_all`` (per P1.3's precedent in
``test_workflow_compose_and_run_row.py``) and an isolated ``AO_OUTPUT_DIR``
so we never touch the developer's filesystem.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base
from app.models.workflow_run import WorkflowStepAsset
from app.services.ao import asset_writer, quality_engine, scheduler_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ao_output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``AO_OUTPUT_DIR`` at ``tmp_path/ao-output`` and reset the settings cache."""
    output_dir = tmp_path / "ao-output"
    output_dir.mkdir()
    monkeypatch.setenv("AO_HOME_DIR", str(tmp_path / "ao-home"))
    monkeypatch.setenv("AO_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("AO_WORKFLOWS_DIR", str(tmp_path / "ao-home" / "workflows"))
    get_settings.cache_clear()
    return output_dir


@pytest.fixture
async def sqlite_session() -> AsyncSession:
    """Yield a fresh sqlite session with the asset + workflow + agent tables created.

    Mirrors the JSONB→JSON + server_default swap idiom used in
    ``test_workflow_compose_and_run_row.py`` so SQLite can compile the
    DDL.
    """
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB

    from app.models.agent import Agent
    from app.models.project import ProjectWorkflow
    from app.models.workflow_run import WorkflowStepAsset as _Asset

    jsonb_swaps: dict = {}
    server_default_swaps: dict = {}
    for table in (ProjectWorkflow.__table__, _Asset.__table__, Agent.__table__):
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
                tables=[ProjectWorkflow.__table__, _Asset.__table__, Agent.__table__],
            )
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as session:
            yield session
    finally:
        for column, original in jsonb_swaps.items():
            column.type = original
        for column, original in server_default_swaps.items():
            column.server_default = original
        await engine.dispose()


async def _ensure_workflow_row(
    sqlite_session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """Insert a minimal ``project_workflows`` row so the FK target exists."""
    from app.models.project import ProjectWorkflow

    workflow = ProjectWorkflow(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        creator_id=uuid.uuid4(),
        name="AI Launch Plan",
        template_key="hr_generated",
        requirements="Build the AI launch pipeline.",
        status="active",
        team_plan={"roles": []},
    )
    sqlite_session.add(workflow)
    await sqlite_session.flush()
    return workflow


# ---------------------------------------------------------------------------
# 1. write_step_asset — file + DB row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_step_asset_writes_file_and_db_row(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    workflow = await _ensure_workflow_row(sqlite_session)
    step_id = uuid.uuid4()
    body = "hello world"

    result = await asset_writer.write_step_asset(
        sqlite_session,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        step_id=step_id,
        category="execution",
        subdir="assets",
        filename="artifact.md",
        content=body,
        metadata={"kind": "executor"},
    )

    expected_path = ao_output_root / str(workflow.id) / "01-执行" / "assets" / "artifact.md"
    assert result["abs_path"] == str(expected_path)
    assert result["rel_path"] == "01-执行/assets/artifact.md"
    assert result["byte_size"] == len(body.encode("utf-8"))
    assert result["hash"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert expected_path.read_text(encoding="utf-8") == body

    row = (
        await sqlite_session.execute(
            select(WorkflowStepAsset).where(WorkflowStepAsset.id == uuid.UUID(result["asset_id"]))
        )
    ).scalar_one()
    assert row.workflow_id == workflow.id
    assert row.step_id == step_id
    assert row.category == "execution"
    assert row.rel_path == result["rel_path"]
    assert row.byte_size == len(body.encode("utf-8"))
    assert row.content_hash == result["hash"]
    assert row.asset_metadata == {"kind": "executor"}


@pytest.mark.asyncio
async def test_write_step_asset_accepts_bytes_payload(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    workflow = await _ensure_workflow_row(sqlite_session)
    payload = b"\x00\x01\x02 binary"

    result = await asset_writer.write_step_asset(
        sqlite_session,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        step_id=None,
        category="delivery",
        subdir="assets",
        filename="artifact.bin",
        content=payload,
    )

    assert result["byte_size"] == len(payload)
    assert (ao_output_root / str(workflow.id) / "03-交付" / "assets" / "artifact.bin").read_bytes() == payload


@pytest.mark.asyncio
async def test_write_step_asset_rejects_path_traversal(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    workflow = await _ensure_workflow_row(sqlite_session)

    with pytest.raises(asset_writer.AssetWriterError):
        await asset_writer.write_step_asset(
            sqlite_session,
            workflow_id=workflow.id,
            tenant_id=workflow.tenant_id,
            step_id=None,
            category="quality",
            subdir="..",
            filename="escape.md",
            content="nope",
        )


# ---------------------------------------------------------------------------
# 2. write_readme — overwrite semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_readme_overwrites_stage_readme(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    workflow = await _ensure_workflow_row(sqlite_session)
    readme_path = ao_output_root / str(workflow.id) / "00-需求" / "README.md"
    # Pre-populate so we can prove the overwrite semantics.
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text("stale body", encoding="utf-8")

    result = await asset_writer.write_readme(
        sqlite_session,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        category="requirement",
        body="# 需求\n\nv2 body",
    )

    assert result["abs_path"] == str(readme_path)
    assert readme_path.read_text(encoding="utf-8") == "# 需求\n\nv2 body"
    assert result["hash"] == hashlib.sha256("# 需求\n\nv2 body".encode()).hexdigest()

    row = (
        await sqlite_session.execute(select(WorkflowStepAsset).where(WorkflowStepAsset.rel_path == "00-需求/README.md"))
    ).scalar_one()
    assert row.asset_metadata == {"kind": "readme"}


# ---------------------------------------------------------------------------
# 3. sync_workflow_assets — scan + insert + orphan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_workflow_assets_inserts_new_files_and_flags_orphans(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    workflow = await _ensure_workflow_row(sqlite_session)
    step_id = uuid.uuid4()

    # Worker drops two artefacts directly to disk — DB is empty.
    quality_dir = ao_output_root / str(workflow.id) / "02-质控" / "assets"
    quality_dir.mkdir(parents=True, exist_ok=True)
    (quality_dir / "step_report.md").write_text("# report\nscore: 92", encoding="utf-8")
    (quality_dir / "trace.json").write_text('{"steps": 2}', encoding="utf-8")

    # Pre-stage a row that points at a file we'll then delete to exercise
    # the orphan path.
    orphan_rel = "01-执行/assets/will_vanish.md"
    orphan_path = ao_output_root / str(workflow.id) / orphan_rel
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_text("temp", encoding="utf-8")
    orphan_row = WorkflowStepAsset(
        id=uuid.uuid4(),
        tenant_id=workflow.tenant_id,
        workflow_id=workflow.id,
        step_id=step_id,
        category="execution",
        rel_path=orphan_rel,
        abs_path=str(orphan_path),
        byte_size=len("temp"),
        content_hash=hashlib.sha256(b"temp").hexdigest(),
        asset_metadata={},
    )
    sqlite_session.add(orphan_row)
    await sqlite_session.flush()
    orphan_path.unlink()

    summary = await asset_writer.sync_workflow_assets(
        sqlite_session,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
    )

    inserted_paths = {row["rel_path"] for row in summary["inserted"]}
    assert "02-质控/assets/step_report.md" in inserted_paths
    assert "02-质控/assets/trace.json" in inserted_paths
    assert summary["orphaned"] == [
        {
            "asset_id": str(orphan_row.id),
            "rel_path": orphan_rel,
            "category": "execution",
        }
    ]

    refreshed_orphan = await sqlite_session.get(WorkflowStepAsset, orphan_row.id)
    assert refreshed_orphan is not None
    assert refreshed_orphan.asset_metadata == {"orphaned": True}


@pytest.mark.asyncio
async def test_sync_workflow_assets_does_not_create_missing_stage_rows(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    """A workflow with no stage dirs scanned yields no inserts and no orphans."""
    workflow = await _ensure_workflow_row(sqlite_session)
    (ao_output_root / str(workflow.id)).mkdir(parents=True, exist_ok=True)

    summary = await asset_writer.sync_workflow_assets(
        sqlite_session,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
    )

    assert summary["scanned"] == []
    assert summary["inserted"] == []
    assert summary["orphaned"] == []


# ---------------------------------------------------------------------------
# 4. Stage directories 00..03 — init + integration with scheduler_tools
# ---------------------------------------------------------------------------


def test_workflow_root_creates_all_four_stage_dirs(
    ao_output_root: Path,
) -> None:
    """``workflow_root`` + ``scheduler_tools.init_workflow_dir`` together create 00..03."""
    workflow_id = str(uuid.uuid4())
    scheduler_tools.init_workflow_dir(workflow_id)

    run_dir = ao_output_root / workflow_id
    assert run_dir.is_dir()
    for stage in ("00-需求", "01-执行", "02-质控", "03-交付"):
        assert (run_dir / stage).is_dir(), f"missing stage {stage}"
        assert (run_dir / stage / "README.md").is_file()


# ---------------------------------------------------------------------------
# 5. Light hooks — quality_engine + scheduler_tools README refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_quality_check_persists_feedback(
    sqlite_session: AsyncSession,
    ao_output_root: Path,
) -> None:
    workflow = await _ensure_workflow_row(sqlite_session)
    step_id = uuid.uuid4()
    verdict = quality_engine.QualityVerdict(
        score=92,
        passed=True,
        feedback="结构完整；建议补充验收测试。",
        per_rule=(),
    )

    await quality_engine.run_quality_check_with_verdict(
        sqlite_session,
        workflow_id=workflow.id,
        tenant_id=workflow.tenant_id,
        verdict=verdict,
        step_id=step_id,
    )

    feedback_path = ao_output_root / str(workflow.id) / "02-质控" / "feedback" / f"step_{step_id}.md"
    assert feedback_path.is_file()
    assert "92" in feedback_path.read_text(encoding="utf-8")
    row = (
        await sqlite_session.execute(
            select(WorkflowStepAsset).where(WorkflowStepAsset.rel_path == f"02-质控/feedback/step_{step_id}.md")
        )
    ).scalar_one()
    assert row.asset_metadata == {"score": 92}


@pytest.mark.asyncio
async def test_dispatch_task_to_role_writes_execution_readme(
    monkeypatch: pytest.MonkeyPatch,
    ao_output_root: Path,
) -> None:
    """``dispatch_task_to_role`` should refresh ``01-执行/README.md`` as a side-effect.

    The hook must remain best-effort — even when the underlying
    ``_load_dispatch_scope`` lookup fails, the dispatch response should
    surface the error rather than the README refresh failure.
    """
    tenant_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    scheduler_agent_id = uuid.uuid4()
    target_agent_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    sender_participant_id = uuid.uuid4()
    target_participant_id = uuid.uuid4()

    added: list = []
    pending_flushes = 0

    async def fake_flush() -> None:
        nonlocal pending_flushes
        pending_flushes += 1

    class _FakeDB:
        pass

    db = _FakeDB()
    db.added = added
    db.flush_count = lambda: pending_flushes
    db.flush = fake_flush  # type: ignore[method-assign]
    db.add = lambda obj: added.append(obj)  # type: ignore[method-assign]
    db.get = lambda *args, **kwargs: None  # type: ignore[method-assign]

    scope = SimpleNamespace(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        group_id=group_id,
        session_id=session_id,
        scheduler_agent_id=scheduler_agent_id,
        creator_id=creator_id,
        sender_participant_id=sender_participant_id,
        target_participant_id=target_participant_id,
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
        user_id=creator_id,
    ):
        result = await scheduler_tools.dispatch_task_to_role(
            str(target_agent_id),
            "整理需求基线",
            {"source": "brief.md"},
        )

    assert result["ok"] is True
    readme = ao_output_root / str(workflow_id) / "01-执行" / "README.md"
    assert readme.is_file(), "execution README should be written by the hook"
    body = readme.read_text(encoding="utf-8")
    assert "整理需求基线" in body
    assert str(target_agent_id) in body
