"""GroupPlanner: builds GroupProposal + OKRProposal + TaskProposal from the user's goal.

Uses the real LLM to generate content tailored to the user's stated goal
(Chinese or English). Falls back to a structured heuristic if the LLM is
unreachable or returns unparseable output.
"""
from __future__ import annotations
import json
import logging
import re
from app.services.orchestrator.draft_schemas import (
    AgentProposal,
    DraftSummary,
    GroupProposal,
    OKRProposal,
    KeyResultProposal,
    TaskProposal,
)

logger = logging.getLogger(__name__)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))
    return text.strip()


def _try_parse_json(text: str):
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except Exception:
        # Try to find the first {...} block in case LLM added extra prose
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


class GroupPlanner:
    """Plan a Group + OKR + initial Tasks for the user's goal.

    The injected `llm_caller` is the SAME stub the rest of the orchestrator
    uses (so the same tenant_id is honored). When the LLM responds with
    parseable JSON, the returned fields are used verbatim. Otherwise we
    fall back to a goal-keyword heuristic that still produces content
    tailored to the user's stated goal (better than a constant stub).
    """

    async def plan(
        self,
        *,
        summary: DraftSummary,
        members: list[AgentProposal],
        user_message: str,
    ) -> tuple[GroupProposal, OKRProposal | None, list[TaskProposal]]:
        llm_callable = getattr(self, "llm_caller", None)
        if llm_callable is not None:
            try:
                plan = await self._plan_with_llm(
                    llm_callable, summary, members, user_message
                )
                if plan is not None:
                    return plan
            except Exception as exc:
                logger.warning(f"GroupPlanner LLM path failed; using heuristic: {exc}")
        return self._plan_heuristic(summary, members, user_message)

    async def _plan_with_llm(
        self,
        llm_callable,
        summary: DraftSummary,
        members: list[AgentProposal],
        user_message: str,
    ) -> tuple[GroupProposal, OKRProposal | None, list[TaskProposal]] | None:
        member_lines = "\n".join(
            f"  - role={m.role!r}, name={m.name!r}" for m in members
        )
        prompt = (
            "You are a project planning assistant for a Chinese/English-speaking team.\n"
            f"User goal: {user_message!r}\n"
            f"Detected intent category: {summary.intent_category}\n"
            f"Team members:\n{member_lines}\n\n"
            "Return a JSON object with EXACTLY these keys (no other text):\n"
            "{\n"
            '  "group_name": "<=40-char project name in the user\'s language>",\n'
            '  "group_description": "<=200-char description in the user\'s language>",\n'
            '  "okr_objective": "<=80-char objective that captures success criteria in the user\'s language>",\n'
            '  "okr_description": "<=200-char context for the objective in the user\'s language>",\n'
            '  "key_results": [\n'
            '    {"title": "<=60-char concrete measurable outcome>", "target_value": 100, "unit": "%", "acceptance_artifact_paths": ["group_files/kr1.md"]}\n'
            "  ],\n"
            '  "tasks": [\n'
            '    {"title": "<=60-char concrete actionable task>", "description": "<=200-char task description>", "assignee_role": "<one of the team roles>", "estimated_artifacts": ["group_files/<file>.md"]}\n'
            "  ]\n"
            "}\n\n"
            "Requirements:\n"
            "- All text in the SAME LANGUAGE as the user goal (Chinese goal -> Chinese output).\n"
            "- Objective + key results must be CONCRETE and MEASURABLE, not generic placeholders.\n"
            "- Tasks must be ACTIONABLE deliverables a single agent can complete, not 'first phase work'.\n"
            "- Return ONLY the JSON object. No prose, no Markdown fences, no explanations."
        )
        raw = await llm_callable(prompt)
        data = _try_parse_json(raw)
        if not isinstance(data, dict):
            return None

        # Validate and coerce
        group_name = (data.get("group_name") or summary.scope_summary or "新项目")[:80]
        group_desc = (data.get("group_description") or user_message or "")[:500]
        okr_objective = (data.get("okr_objective") or summary.scope_summary or "项目目标")[:80]
        okr_desc = (data.get("okr_description") or user_message or "")[:200]

        krs_raw = data.get("key_results") or []
        krs: list[KeyResultProposal] = []
        for kr in krs_raw[:4]:
            if not isinstance(kr, dict) or not kr.get("title"):
                continue
            krs.append(
                KeyResultProposal(
                    title=str(kr["title"])[:200],
                    target_value=float(kr.get("target_value", 100.0) or 100.0),
                    unit=kr.get("unit"),
                    acceptance_artifact_paths=list(kr.get("acceptance_artifact_paths") or [])[:5],
                )
            )
        if not krs:
            return None
        okr = OKRProposal(
            objective_title=okr_objective,
            objective_description=okr_desc,
            key_results=krs,
        )

        valid_roles = {m.role for m in members if m.role}
        tasks: list[TaskProposal] = []
        for t in (data.get("tasks") or [])[:8]:
            if not isinstance(t, dict) or not t.get("title"):
                continue
            role = t.get("assignee_role")
            if role not in valid_roles:
                # Default to the first non-chief role
                role = next((m.role for m in members if m.role and m.role != "chief-of-staff"), None) or (members[0].role if members else None)
            tasks.append(
                TaskProposal(
                    title=str(t["title"])[:200],
                    description=str(t.get("description") or "")[:500],
                    assignee_role=role,
                    estimated_artifacts=list(t.get("estimated_artifacts") or [])[:5],
                )
            )
        if not tasks:
            return None
        return (
            GroupProposal(name=group_name, description=group_desc),
            okr,
            tasks,
        )

    def _plan_heuristic(
        self,
        summary: DraftSummary,
        members: list[AgentProposal],
        user_message: str,
    ) -> tuple[GroupProposal, OKRProposal | None, list[TaskProposal]]:
        """Goal-aware fallback (better than a constant stub).

        Extracts a 1-line "project name" from the first sentence of the
        user goal, and creates a goal-specific initial task per team role.
        """
        first_line = user_message.strip().splitlines()[0].strip() if user_message else ""
        group_name = (first_line[:80] or summary.scope_summary or "新项目").rstrip("。.!?")
        # Default 2 KRs: research/planning + execution
        krs = [
            KeyResultProposal(
                title=f"完成 {group_name} 的研究 + 方案",
                target_value=100.0,
                unit="%",
                acceptance_artifact_paths=["group_files/plan_draft.md"],
            ),
            KeyResultProposal(
                title=f"交付 {group_name} 的最终成果",
                target_value=100.0,
                unit="%",
                acceptance_artifact_paths=["group_files/deliverable.md"],
            ),
        ]
        okr = OKRProposal(
            objective_title=f"完成 {group_name}",
            objective_description=user_message[:500],
            key_results=krs,
        )
        tasks = [
            TaskProposal(
                title=f"{m.name} 完成 {group_name} 中的 {m.role} 职责",
                description=f"基于 {summary.intent_category or '相关'} 类别推进: {user_message[:200]}",
                assignee_role=m.role,
                estimated_artifacts=[f"group_files/{m.role}-output.md"],
            )
            for m in members
            if m.role != "chief-of-staff"
        ]
        return GroupProposal(name=group_name, description=user_message[:500]), okr, tasks
