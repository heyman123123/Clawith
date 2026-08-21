"""Reasoner: consumes events + group state, produces ActionPlan via LLM.

Uses injected LLM caller (C4 - no direct API imports). Falls back to a
cheaper model if primary fails. Safe no-op on parse failure.
"""
from __future__ import annotations
import json
import logging
from typing import Callable, Awaitable
from app.services.agent_runtime.orchestrator.checkpoint_state import (
    ActionPlan,
    GroupState,
)

logger = logging.getLogger(__name__)

LLMCaller = Callable[[str], Awaitable[str]]

_PROMPT = """你是 Clawith Chief of Staff。基于 Group 事件 + 当前状态,产出 ActionPlan(JSON):
事件: {event}
Group 状态(schema): {state_schema}
返回字段:
- review_artifact_paths: 需 Chief 审核的群文件路径列表
- move_cards: [{card_id, to_column, expected_version}] (column: backlog/in_progress/blocked/review/done)
- assign_cards: [{card_id, assignee_agent_id, expected_version}]
- dispatch_tasks: [{agent_role, title, description}]
- update_okr_progress: [{key_result_id, progress_delta, note}]
- notify_user: 给用户的简短消息(可空)

只返回 JSON,不要其他文本。
"""


class Reasoner:
    def __init__(
        self,
        llm_caller: LLMCaller,
        fallback_caller: LLMCaller | None = None,
    ) -> None:
        self.llm_caller = llm_caller
        self.fallback_caller = fallback_caller

    async def decide(self, *, event, group_state: GroupState) -> ActionPlan:
        prompt = (
            _PROMPT
            .replace("{event}", str(getattr(event, "__dict__", event)))
            .replace("{state_schema}", str(GroupState.model_json_schema()))
        )
        try:
            raw = await self._call_with_fallback(prompt)
        except Exception as exc:
            logger.warning("Reasoner LLM call failed: %s", exc)
            return ActionPlan()  # safe no-op
        return self._parse(raw)

    def _parse(self, raw: str) -> ActionPlan:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(l for l in lines if not l.startswith("```"))
        try:
            data = json.loads(text)
            return ActionPlan(**data)
        except Exception as exc:
            logger.warning("Reasoner parse failed: %s; raw=%r", exc, raw[:200])
            return ActionPlan()

    async def _call_with_fallback(self, prompt: str) -> str:
        try:
            return await self.llm_caller(prompt)
        except Exception:
            if self.fallback_caller is None:
                raise
            return await self.fallback_caller(prompt)
