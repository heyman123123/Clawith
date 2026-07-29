"""List workflow assets by eight-bucket category (需求 §4.7 / §8.5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.project import ProjectWorkflow
from app.models.user import User
from app.models.workflow_run import WorkflowStepAsset
from app.services.ao.asset_directory_enforcer import AssetCategory, canonical_directory_set
from app.services.ao.asset_writer import sync_workflow_assets
from app.services.security_shell import assert_tenant_owns

router = APIRouter(prefix="/ao", tags=["ao-assets"])


def _tenant_id(user: User) -> uuid.UUID:
    tid = getattr(user, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant required")
    return tid


@router.get("/workflows/{workflow_id}/assets")
async def list_workflow_assets(
    workflow_id: uuid.UUID,
    category: str | None = Query(default=None),
    sync: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return persisted step assets for a workflow, optionally filtered by category."""
    tenant_id = _tenant_id(current_user)
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    assert_tenant_owns(
        actor_tenant_id=str(tenant_id),
        record_tenant_id=str(workflow.tenant_id),
        context="workflow assets",
    )

    if sync:
        try:
            await sync_workflow_assets(db, workflow_id=workflow_id, tenant_id=tenant_id)
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()

    stmt = select(WorkflowStepAsset).where(
        WorkflowStepAsset.workflow_id == workflow_id,
        WorkflowStepAsset.tenant_id == tenant_id,
    )
    if category:
        if category in {"requirement", "execution", "quality", "delivery"}:
            stmt = stmt.where(WorkflowStepAsset.category == category)
        else:
            stmt = stmt.where(WorkflowStepAsset.rel_path.startswith(f"{category}/"))
    rows = (await db.scalars(stmt.order_by(WorkflowStepAsset.created_at.asc()))).all()
    items = [
        {
            "id": str(r.id),
            "category": r.category,
            "rel_path": r.rel_path,
            "byte_size": r.byte_size or 0,
            "hash": r.content_hash or "",
            "orphaned": bool((r.asset_metadata or {}).get("orphaned"))
            if isinstance(r.asset_metadata, dict)
            else False,
        }
        for r in rows
    ]
    return {
        "workflow_id": str(workflow_id),
        "buckets": sorted(canonical_directory_set()),
        "categories": [m.value for m in AssetCategory],
        "items": items,
        "count": len(items),
    }


@router.get("/workflows/{workflow_id}/token-usage")
async def get_workflow_token_usage(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return step-level + workflow-level token counters (需求 §4.8)."""
    from app.services.ao.token_audit import get_workflow_token_report

    tenant_id = _tenant_id(current_user)
    report = await get_workflow_token_report(
        db, workflow_id=workflow_id, tenant_id=tenant_id
    )
    if not report.get("ok"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    return report
