"""AgentSelector: reuses existing templates when possible; LLM-generates missing roles.

Strategy:
  1. Search existing AgentTemplate rows (filtered by intent_category + tenant scope)
  2. Identify role gaps vs the canonical role set for the intent category
  3. If gaps exist, ask the LLM to generate new template specs
  4. Always include the chief-of-staff (PM) as the group coordinator
"""
from __future__ import annotations
import logging
from typing import Callable, Awaitable
from uuid import UUID
from app.services.orchestrator.draft_schemas import AgentProposal, DraftSummary

logger = logging.getLogger(__name__)

# Canonical role set per intent category (lightweight heuristic; refine via learned model later)
CANONICAL_ROLES: dict[str, list[str]] = {
    "growth_strategy": ["researcher", "analyst", "writer"],
    "engineering_task": ["architect", "developer", "reviewer"],
    "market_research": ["researcher", "analyst"],
    "monitoring": ["watcher", "reporter"],
    "creative": ["ideator", "writer"],
    "operations": ["planner", "executor"],
    "other": ["assistant"],
}


class AgentSelector:
    def __init__(
        self,
        template_search: Callable[[str, UUID], Awaitable[list[dict]]],
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
        existing = await self.template_search(summary.intent_category, tenant_id) if tenant_id else await self.template_search(summary.intent_category, None)
        proposals = [
            AgentProposal(
                role=t.get("role", "agent"),
                name=t.get("name", "Agent"),
                system_prompt=t.get("system_prompt", ""),
                template_id=t.get("template_id"),
                is_new_template=False,
            )
            for t in existing
        ]

        filled_roles = {p.role for p in proposals}
        target_roles = CANONICAL_ROLES.get(summary.intent_category, ["assistant"])
        role_gaps = [r for r in target_roles if r not in filled_roles]

        if role_gaps:
            generated = await self.llm_generate_template(summary.intent_category, role_gaps)
            for g in generated:
                proposals.append(
                    AgentProposal(
                        role=g.get("role", "agent"),
                        name=g.get("name", "Agent"),
                        system_prompt=g.get("system_prompt", ""),
                        template_id=None,
                        is_new_template=True,
                    )
                )

        if always_include_chief and "chief-of-staff" not in {p.role for p in proposals}:
            proposals.insert(
                0,
                AgentProposal(
                    role="chief-of-staff",
                    name="Chief of Staff",
                    system_prompt="You are the Chief of Staff coordinating this group.",
                    template_id=None,
                    is_new_template=False,
                ),
            )

        return proposals
