"""P2.3 / P7 — workflow 八类资产目录自动写 + 扫描同步.

The module is the single boundary for *writing* assets under
``{AO_OUTPUT_DIR}/<workflow_id>/<stage>/`` and for *mirroring* the file
system back into the ``workflow_step_assets`` table.  The conventions
(需求 §4.7):

* Eight buckets: ``00-工作流定义`` … ``07-历史迭代``.
* Legacy writer keys (``requirement`` / ``execution`` / ``quality`` /
  ``delivery``) map onto the canonical buckets via :data:`_STAGE_DIRS`.

The :class:`Category` literal matches the migration column.  The mapping
between Chinese directory names and the canonical English category
stays in :data:`_STAGE_DIRS` so the rest of the module never has to
re-derive it.

Two important properties:

1. **Write side-effect order:** ``write_step_asset`` writes the file
   *first* then stages the DB row.  A failure during the DB step raises
   ``AssetWriterError``; the file may already exist on disk so the next
   call (or ``sync_workflow_assets``) re-discovers it.  We do not roll
   back the file because filesystem half-state is preferable to leaving
   the caller with a confusing exception.
2. **Scan side-effect:** ``sync_workflow_assets`` is idempotent and
   conservative.  New rows are inserted; rows whose ``abs_path`` no
   longer exists are marked ``metadata={'orphaned': True}`` (the column
   itself is immutable in shape — we keep the row so audit history is
   not lost).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.services.ao.asset_directory_enforcer import AssetCategory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.workflow_run import WorkflowStepAsset


Category = Literal["requirement", "execution", "quality", "delivery"]
"""Canonical stage key written into ``workflow_step_assets.category``."""

_STAGE_DIRS: tuple[tuple[Category, str], ...] = (
    ("requirement", AssetCategory.WORKFLOW_DEFINITION.value),
    ("execution", AssetCategory.STEP_OUTPUT.value),
    ("quality", AssetCategory.QUALITY_CONTROL.value),
    ("delivery", AssetCategory.DELIVERY_REVIEW.value),
)
"""(category, on-disk directory name) mapping.  Mirrors 需求 §4.7 via AssetCategory."""

_STAGE_DIR_BY_CATEGORY: dict[Category, str] = {key: value for key, value in _STAGE_DIRS}


class AssetWriterError(RuntimeError):
    """Raised when an asset write or scan operation cannot complete."""


def _ao_output_dir() -> Path:
    """Resolve the per-workflow output root from the live settings."""
    cfg = get_settings()
    base = Path(cfg.AO_OUTPUT_DIR or "")
    if not base:
        base = Path(cfg.AO_HOME_DIR or ".") / "output"
    return base


def category_dir(workflow_id: uuid.UUID | str, category: Category) -> Path:
    """Return ``<AO_OUTPUT_DIR>/<workflow_id>/<stage>/`` and create the parent."""
    if category not in _STAGE_DIR_BY_CATEGORY:
        raise AssetWriterError(
            f"Unknown category {category!r}; expected one of {sorted(_STAGE_DIR_BY_CATEGORY)}"
        )
    stage_name = _STAGE_DIR_BY_CATEGORY[category]
    base = _ao_output_dir()
    workflow_dir = base / str(workflow_id)
    stage_dir = workflow_dir / stage_name
    stage_dir.mkdir(parents=True, exist_ok=True)
    return stage_dir


def workflow_root(workflow_id: uuid.UUID | str) -> Path:
    """Return the per-workflow output root, creating the directory if needed.

    Public so ``init_workflow_dir`` callers (and tests) can locate the
    root without re-deriving :data:`_STAGE_DIRS`.
    """
    base = _ao_output_dir()
    workflow_dir = base / str(workflow_id)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    return workflow_dir


def _safe_subpath(*parts: str) -> Path:
    """Reject subpaths that try to escape the category directory.

    ``write_step_asset`` accepts arbitrary ``subdir`` + ``filename`` so
    a misconfigured caller could write outside the stage directory.
    Reject ``..`` traversal up front; safer than the alternative of
    silently allowing ``../../etc/...`` paths.
    """
    for piece in parts:
        if piece in {"", ".", ".."} or "/" in piece or "\\" in piece:
            raise AssetWriterError(f"Unsafe path component: {piece!r}")
    return Path(*parts)


def _relative_to_workflow(workflow_id: uuid.UUID | str, abs_path: Path) -> str:
    """Compute ``<stage>/<subdir>/<filename>`` for storage in ``rel_path``.

    The leading ``<workflow_id>/`` segment is *not* included — the
    column only needs to be unique within a workflow (the
    ``(workflow_id, category)`` index already prefixes it) and the
    shortened path keeps the column under the 512-byte limit for deep
    stage trees.  Callers that need the full path join ``workflow_id``
    + ``rel_path`` themselves.
    """
    return str(abs_path.relative_to(workflow_root(workflow_id)))


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _encode_content(content: str | bytes) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    return content


async def write_step_asset(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    step_id: uuid.UUID | None,
    category: Category,
    subdir: str,
    filename: str,
    content: str | bytes,
    metadata: dict | None = None,
) -> dict:
    """Write ``<category>/<subdir>/<filename>`` and stage a ``WorkflowStepAsset`` row.

    Returns ``{abs_path, rel_path, hash, byte_size, asset_id}`` so the
    caller can surface the canonical reference in group messages or in
    tool-call responses.

    The function does not call ``commit`` — it follows the
    ``run_repository`` convention of letting the provisioning/kickoff
    transaction own the boundary.
    """
    from app.models.workflow_run import WorkflowStepAsset

    stage_dir = category_dir(workflow_id, category)
    relative_dir = _safe_subpath(subdir)
    relative_file = relative_dir / _safe_subpath(filename)
    target_dir = stage_dir / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    abs_path = stage_dir / relative_file

    payload = _encode_content(content)
    abs_path.write_bytes(payload)
    byte_size = len(payload)
    content_hash = _hash_bytes(payload)
    rel_path = _relative_to_workflow(workflow_id, abs_path)

    row = WorkflowStepAsset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        step_id=step_id,
        category=category,
        rel_path=rel_path,
        abs_path=str(abs_path),
        byte_size=byte_size,
        content_hash=content_hash,
        asset_metadata=metadata or {},
    )
    db.add(row)
    await db.flush()
    logger.info(
        "[AOAssetWriter] Wrote {} bytes to {} (sha256={})",
        byte_size,
        abs_path,
        content_hash[:12],
    )
    return {
        "asset_id": str(row.id),
        "abs_path": str(abs_path),
        "rel_path": rel_path,
        "hash": content_hash,
        "byte_size": byte_size,
    }


async def write_readme(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    category: Category,
    body: str,
    step_id: uuid.UUID | None = None,
) -> dict:
    """Overwrite ``<stage>/README.md`` and stage a row.

    README files are intentionally **always** rewritten in full so the
    scheduler / executor can rely on a single source of truth per stage.
    """
    stage_dir = category_dir(workflow_id, category)
    readme_path = stage_dir / "README.md"
    payload = _encode_content(body)
    readme_path.write_bytes(payload)
    byte_size = len(payload)
    content_hash = _hash_bytes(payload)
    rel_path = _relative_to_workflow(workflow_id, readme_path)

    from app.models.workflow_run import WorkflowStepAsset

    row = WorkflowStepAsset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        step_id=step_id,
        category=category,
        rel_path=rel_path,
        abs_path=str(readme_path),
        byte_size=byte_size,
        content_hash=content_hash,
        asset_metadata={"kind": "readme"},
    )
    db.add(row)
    await db.flush()
    logger.info(
        "[AOAssetWriter] Wrote README for {} ({} bytes)",
        category,
        byte_size,
    )
    return {
        "asset_id": str(row.id),
        "abs_path": str(readme_path),
        "rel_path": rel_path,
        "hash": content_hash,
        "byte_size": byte_size,
    }


async def _list_existing_rows(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
) -> dict[str, WorkflowStepAsset]:
    from app.models.workflow_run import WorkflowStepAsset

    result = await db.execute(
        select(WorkflowStepAsset).where(WorkflowStepAsset.workflow_id == workflow_id)
    )
    return {row.rel_path: row for row in result.scalars().all()}


def _scan_category_dir(stage_dir: Path) -> list[Path]:
    """Yield every regular file under ``stage_dir`` recursively, excluding the README placeholder.

    The README file is the *target* of ``write_readme`` — counting it
    here would cause ``sync_workflow_assets`` to skip re-staging rows
    when the operator only ran ``write_readme``.  We still allow
    non-default names so custom README copies can be tracked.
    """
    if not stage_dir.is_dir():
        return []
    files: list[Path] = []
    for entry in stage_dir.rglob("*"):
        if entry.is_file():
            files.append(entry)
    return files


async def sync_workflow_assets(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    """Reconcile the file system under ``ao-output/<workflow_id>/`` with the DB.

    Returns ``{scanned: [...], inserted: [...], orphaned: [...]}`` so the
    scheduler can surface a summary in the run timeline.
    """
    from app.models.workflow_run import WorkflowStepAsset

    root = workflow_root(workflow_id)
    existing = await _list_existing_rows(db, workflow_id=workflow_id)
    scanned: list[dict] = []
    inserted: list[dict] = []
    orphaned: list[dict] = []

    for category, stage_name in _STAGE_DIRS:
        stage_dir = root / stage_name
        if not stage_dir.is_dir():
            # We still record an audit row so the UI can flag "stage not
            # initialised" — kept lightweight, the rel_path is the
            # directory itself.
            continue
        for file_path in _scan_category_dir(stage_dir):
            try:
                rel_path = str(file_path.relative_to(root))
            except ValueError:  # pragma: no cover - defensive
                continue
            payload = file_path.read_bytes()
            content_hash = _hash_bytes(payload)
            byte_size = len(payload)
            scanned.append(
                {
                    "category": category,
                    "rel_path": rel_path,
                    "byte_size": byte_size,
                    "hash": content_hash,
                }
            )
            row = existing.get(rel_path)
            if row is None:
                new_row = WorkflowStepAsset(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    step_id=None,
                    category=category,
                    rel_path=rel_path,
                    abs_path=str(file_path),
                    byte_size=byte_size,
                    content_hash=content_hash,
                    asset_metadata={"synced": True},
                )
                db.add(new_row)
                inserted.append({"rel_path": rel_path, "category": category})
                continue
            # Update the on-disk view: bump hash/size if the file
            # changed; never overwrite ``step_id`` / ``category``.
            row.byte_size = byte_size
            row.content_hash = content_hash
            row.abs_path = str(file_path)
            merged_meta = dict(row.asset_metadata or {})
            merged_meta["synced_at"] = "scan"
            row.asset_metadata = merged_meta

    for rel_path, row in existing.items():
        abs_path = Path(row.abs_path)
        if not abs_path.exists():
            merged_meta = dict(row.asset_metadata or {})
            merged_meta["orphaned"] = True
            row.asset_metadata = merged_meta
            orphaned.append(
                {
                    "asset_id": str(row.id),
                    "rel_path": rel_path,
                    "category": row.category,
                }
            )

    if inserted or orphaned:
        await db.flush()
    logger.info(
        "[AOAssetWriter] sync_workflow_assets wf={} scanned={} inserted={} orphaned={}",
        workflow_id,
        len(scanned),
        len(inserted),
        len(orphaned),
    )
    return {"scanned": scanned, "inserted": inserted, "orphaned": orphaned}


__all__ = [
    "AssetWriterError",
    "Category",
    "category_dir",
    "sync_workflow_assets",
    "workflow_root",
    "write_readme",
    "write_step_asset",
]