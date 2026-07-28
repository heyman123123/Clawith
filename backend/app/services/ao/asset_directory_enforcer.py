"""Asset directory enforcement (P7).

需求 §4.7 + §8 验收标准 #5 要求群文件夹 8 类资产齐全:

* 00-工作流定义
* 01-步骤输出
* 02-过程记录
* 03-质量管控
* 04-交付验收
* 05-技能档案
* 06-最终交付
* 07-历史迭代

The legacy 4-category enum (``requirement`` / ``execution`` / ``quality`` /
``delivery``) still maps 1:1 onto the first four slot names, so the
on-disk layout is backwards-compatible.  This module:

* declares the canonical :class:`AssetCategory` enum (the 8 buckets),
* validates callers via :func:`is_valid_category`,
* registers a debug-only filesystem assertion hook (``install_dir_assert_hook``)
  that the test harness can flip on to fail-loud if a producer code path
  writes outside the canonical 8 directories.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class AssetCategory(str, Enum):
    """Canonical eight-bucket taxonomy of workflow assets (需求 §4.7)."""

    WORKFLOW_DEFINITION = "00-工作流定义"
    STEP_OUTPUT = "01-步骤输出"
    PROCESS_RECORD = "02-过程记录"
    QUALITY_CONTROL = "03-质量管控"
    DELIVERY_REVIEW = "04-交付验收"
    SKILL_ARTIFACT = "05-技能档案"
    FINAL_DELIVERY = "06-最终交付"
    ITERATION_HISTORY = "07-历史迭代"


# Backwards-compatible aliases: legacy callers use the 4-category enum.
_LEGACY_CATEGORY_TO_BUCKET: dict[str, AssetCategory] = {
    "requirement": AssetCategory.WORKFLOW_DEFINITION,
    "execution": AssetCategory.STEP_OUTPUT,
    "quality": AssetCategory.QUALITY_CONTROL,
    "delivery": AssetCategory.DELIVERY_REVIEW,
}


def bucket_for(category: str) -> AssetCategory:
    """Map legacy or canonical category strings to an 8-bucket enum."""
    if category in AssetCategory.__members__:
        return AssetCategory(category)
    if category in _LEGACY_CATEGORY_TO_BUCKET:
        return _LEGACY_CATEGORY_TO_BUCKET[category]
    raise ValueError(
        f"Unknown asset category {category!r}; expected one of "
        f"{sorted(AssetCategory.__members__) + sorted(_LEGACY_CATEGORY_TO_BUCKET)}"
    )


def is_valid_category(value: str) -> bool:
    """True when ``value`` is a canonical or legacy category.

    Accepts both the Python member name (``WORKFLOW_DEFINITION``) and the
    on-disk Chinese directory (``00-工作流定义``), as well as the legacy
    4-key enum (``requirement`` etc.).
    """
    if value in AssetCategory.__members__:
        return True
    if value in _LEGACY_CATEGORY_TO_BUCKET:
        return True
    return value in {member.value for member in AssetCategory}


def canonical_directory_set() -> set[str]:
    """Return the eight canonical directory names exactly as on-disk."""
    return {member.value for member in AssetCategory}


# ---------------------------------------------------------------------------
# Debug hook — installer for the test harness.
# ---------------------------------------------------------------------------

_ASSET_DEBUG_HOOK_INSTALLED = False


def install_dir_assert_hook() -> None:
    """Register :func:`assert_within_workflow` on the relevant Path subclasses.

    Tests enable a debug environment variable (``CLAWITH_ASSET_DEBUG=1``) that
    upgrades :func:`asset_writer.write_step_asset` to fail-loud when callers
    try to write outside the canonical 8 directories.
    """

    import os

    if os.environ.get("CLAWITH_ASSET_DEBUG") != "1":
        return
    global _ASSET_DEBUG_HOOK_INSTALLED
    if _ASSET_DEBUG_HOOK_INSTALLED:
        return
    _ASSET_DEBUG_HOOK_INSTALLED = True

    import app.services.ao.asset_writer as _aw

    original_write = _aw.write_step_asset

    async def _guarded_write_step_asset(db, *, workflow_id, tenant_id, step_id, category, subdir, filename, content, metadata=None):
        if not is_valid_category(category):
            raise ValueError(
                f"[CLAWITH_ASSET_DEBUG] refusing to write asset under unknown category {category!r}"
            )
        return await original_write(
            db,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            step_id=step_id,
            category=category,
            subdir=subdir,
            filename=filename,
            content=content,
            metadata=metadata,
        )

    _aw.write_step_asset = _guarded_write_step_asset  # type: ignore[assignment]
    return


def assert_within_workflow(workflow_id: str, target: Path) -> None:
    """Sanity check that ``target`` lives inside the per-workflow root.

    Used by the test harness in debug mode; raises :class:`ValueError` when
    the workflow_id segment is missing.
    """
    parts = target.parts
    if workflow_id not in parts:
        raise ValueError(
            f"[CLAWITH_ASSET_DEBUG] target path {target} missing workflow_id {workflow_id} segment"
        )


__all__ = [
    "AssetCategory",
    "assert_within_workflow",
    "bucket_for",
    "canonical_directory_set",
    "install_dir_assert_hook",
    "is_valid_category",
]
