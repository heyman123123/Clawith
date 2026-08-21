"""GroupPlanner: builds GroupProposal + OKRProposal + TaskProposal from summary + members.

Heuristic-driven (not LLM-dependent) for predictability and idempotency.
The LLM only chose the intent category; everything here is structural.
"""
from __future__ import annotations
from app.services.orchestrator.draft_schemas import (
    AgentProposal,
    DraftSummary,
    GroupProposal,
    OKRProposal,
    KeyResultProposal,
    TaskProposal,
)


class GroupPlanner:
    async def plan(
        self,
        *,
        summary: DraftSummary,
        members: list[AgentProposal],
        user_message: str,
    ) -> tuple[GroupProposal, OKRProposal | None, list[TaskProposal]]:
        group = GroupProposal(
            name=summary.scope_summary[:80] or "新项目",
            description=user_message[:500],
        )
        okr = OKRProposal(
            objective_title=summary.scope_summary or "项目目标",
            objective_description=user_message,
            key_results=[
                KeyResultProposal(
                    title="第一阶段:研究 + 方案草稿",
                    target_value=100.0,
                    unit="%",
                    acceptance_artifact_paths=["group_files/plan_draft.md"],
                ),
                KeyResultProposal(
                    title="第二阶段:执行 + 交付",
                    target_value=100.0,
                    unit="%",
                    acceptance_artifact_paths=["group_files/deliverable.md"],
                ),
            ],
        )
        tasks = [
            TaskProposal(
                title=f"{m.name} 接手第一阶段任务",
                description=f"基于 {summary.intent_category} 推进: {summary.scope_summary}",
                assignee_role=m.role,
                estimated_artifacts=["group_files/plan_draft.md"],
            )
            for m in members
            if m.role != "chief-of-staff"
        ]
        return group, okr, tasks
