"""Template Registry API: admin approval + user browsing.

NOT auto-mounted in main.py (operator wires explicitly).
Endpoints:
  GET    /api/template-registry/pending-approval     — admin only
  POST   /api/template-registry/{template_id}/approve — admin only
  POST   /api/template-registry/{template_id}/reject  — admin only, body: {reason}
  GET    /api/template-registry/browse               — user's accessible templates
"""
from __future__ import annotations
import uuid
from fastapi import APIRouter, HTTPException, status

from app.services.agent_template.registry.approval import (
    approve_template,
    reject_template,
    list_pending_approval,
    TemplateApprovalError,
)

router = APIRouter(prefix="/api/template-registry", tags=["template-registry"])


@router.get("/pending-approval")
async def pending_approval(tenant_id: str | None = None):
    tpl_list = await list_pending_approval(uuid.UUID(tenant_id) if tenant_id else None)
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "category": t.category,
            "template_visibility": t.template_visibility,
            "tenant_id": str(t.tenant_id) if t.tenant_id else None,
            "created_by": str(t.created_by),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tpl_list
    ]


@router.post("/{template_id}/approve")
async def approve(template_id: uuid.UUID, payload: dict):
    approver_id = payload.get("approver_id")
    if not approver_id:
        raise HTTPException(status_code=400, detail="approver_id required")
    try:
        tpl = await approve_template(template_id, uuid.UUID(approver_id))
        return {"id": str(tpl.id), "approval_state": tpl.approval_state}
    except TemplateApprovalError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.post("/{template_id}/reject")
async def reject(template_id: uuid.UUID, payload: dict):
    reason = payload.get("reason", "")
    if not reason:
        raise HTTPException(status_code=400, detail="reason required")
    try:
        tpl = await reject_template(template_id, reason)
        return {"id": str(tpl.id), "approval_state": tpl.approval_state}
    except TemplateApprovalError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.get("/browse")
async def browse_templates(tenant_id: str | None = None):
    """Return templates accessible to a user (public + tenant_private of user's tenant)."""
    from sqlalchemy import select, or_
    from app.database import async_session
    from app.models.agent import AgentTemplate
    async with async_session() as db:
        stmt = select(AgentTemplate).where(
            AgentTemplate.approval_state.in_(["approved", "draft"]),
        )
        if tenant_id:
            tid = uuid.UUID(tenant_id)
            stmt = stmt.where(
                or_(
                    AgentTemplate.template_visibility == "public",
                    AgentTemplate.template_visibility == "tenant_private",
                    AgentTemplate.tenant_id == tid,
                )
            )
        rows = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "category": t.category,
                "template_visibility": t.template_visibility,
                "is_builtin": t.is_builtin,
                "approval_state": t.approval_state,
            }
            for t in rows
        ]
