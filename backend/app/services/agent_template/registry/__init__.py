"""Template Registry package - visibility + approval workflow for AgentTemplate."""
from app.services.agent_template.registry.visibility import (
    TemplateVisibility,
    ApprovalState,
)
from app.services.agent_template.registry.approval import (
    approve_template,
    reject_template,
    list_pending_approval,
)
from app.services.agent_template.registry.llm_generator import persist_generated_template

__all__ = [
    "TemplateVisibility",
    "ApprovalState",
    "approve_template",
    "reject_template",
    "list_pending_approval",
    "persist_generated_template",
]
