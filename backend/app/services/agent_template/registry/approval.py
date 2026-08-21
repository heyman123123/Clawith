"""Admin approval workflow for tenant-shared templates."""
from __future__ import annotations
import logging
import uuid
from sqlalchemy import select
from app.database import async_session
from app.models.agent import AgentTemplate

logger = logging.getLogger(__name__)


class TemplateApprovalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def approve_template(template_id: uuid.UUID, approver_id: uuid.UUID) -> AgentTemplate:
    async with async_session() as db:
        tpl = await db.get(AgentTemplate, template_id)
        if tpl is None:
            raise TemplateApprovalError("not_found", f"Template {template_id} not found")
        if tpl.approval_state != "draft":
            raise TemplateApprovalError(
                "invalid_state",
                f"Template is in '{tpl.approval_state}' state; can only approve from 'draft'",
            )
        tpl.approval_state = "approved"
        await db.commit()
        await db.refresh(tpl)
        logger.info("Template %s approved by %s", template_id, approver_id)
        return tpl


async def reject_template(template_id: uuid.UUID, reason: str) -> AgentTemplate:
    async with async_session() as db:
        tpl = await db.get(AgentTemplate, template_id)
        if tpl is None:
            raise TemplateApprovalError("not_found", f"Template {template_id} not found")
        if tpl.approval_state != "draft":
            raise TemplateApprovalError(
                "invalid_state",
                f"Template is in '{tpl.approval_state}' state; can only reject from 'draft'",
            )
        tpl.approval_state = "rejected"
        tpl.rejected_reason = reason[:1000]
        await db.commit()
        await db.refresh(tpl)
        logger.info("Template %s rejected: %s", template_id, reason[:200])
        return tpl


async def list_pending_approval(tenant_id: uuid.UUID | None = None) -> list[AgentTemplate]:
    """List all templates awaiting admin approval (optionally scoped to a tenant)."""
    async with async_session() as db:
        stmt = select(AgentTemplate).where(AgentTemplate.approval_state == "draft")
        if tenant_id is not None:
            stmt = stmt.where(AgentTemplate.tenant_id == tenant_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())
