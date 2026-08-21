"""Persist LLM-generated templates into agent_templates with template_visibility.

User chooses visibility in Draft Preview UI (default user_private).
Templates that need admin approval (tenant_private/public) start as approval_state='draft'.
"""
from __future__ import annotations
import uuid
import logging
from app.database import async_session
from app.models.agent import AgentTemplate

logger = logging.getLogger(__name__)


async def persist_generated_template(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    description: str,
    role: str,
    system_prompt: str,
    template_visibility: str = "user_private",
    soul_template: str = "",
    default_skills: list | None = None,
    default_mcp_servers: list | None = None,
    capability_bullets: list | None = None,
) -> uuid.UUID:
    """Create a new agent_template row from LLM-generated content.

    Returns the new template id.

    Approval state is determined by visibility:
      - user_private -> 'approved' immediately (only creator sees it)
      - tenant_private / public -> 'draft' (admin must approve)
    """
    needs_admin = template_visibility in ("tenant_private", "public")
    template_id = uuid.uuid4()
    async with async_session() as db:
        tpl = AgentTemplate(
            id=template_id,
            tenant_id=tenant_id if template_visibility != "public" else None,
            name=name,
            description=description,
            icon="🤖",
            category=role,
            soul_template=soul_template or system_prompt,
            default_skills=default_skills or [],
            default_mcp_servers=default_mcp_servers or [],
            default_autonomy_policy={},
            capability_bullets=capability_bullets or [],
            is_builtin=False,
            created_by=user_id,
            template_visibility=template_visibility,
            approval_state="draft" if needs_admin else "approved",
        )
        db.add(tpl)
        await db.commit()
        await db.refresh(tpl)
    logger.info(
        "Generated template %s (visibility=%s, approval=%s)",
        template_id,
        template_visibility,
        tpl.approval_state,
    )
    return template_id
