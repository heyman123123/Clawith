"""IntentAnalyzer: parses user natural-language goal into structured DraftSummary.

Uses an injected LLM caller (no direct OpenAI/Anthropic imports — C4).
Falls back to a cheaper model if primary fails. Parses JSON output with
lenient error handling.
"""
from __future__ import annotations
import json
import logging
from typing import Callable, Awaitable
from app.services.orchestrator.draft_schemas import DraftSummary

logger = logging.getLogger(__name__)

LLMCaller = Callable[[str], Awaitable[str]]

_PROMPT = """你是 Clawith 项目意图分析助手。将用户的自然语言需求解析为严格 JSON:
{
  "intent_category": "<growth_strategy|engineering_task|market_research|monitoring|creative|operations|other>",
  "scope_summary": "<一句话总结需求范围,<=80字>",
  "key_constraints": ["<约束1>", "<约束2>"]
}
用户输入: {user_message}
只返回 JSON,不要任何其他文本。
"""


class IntentAnalyzer:
    def __init__(self, llm_caller: LLMCaller, fallback_caller: LLMCaller | None = None) -> None:
        self.llm_caller = llm_caller
        self.fallback_caller = fallback_caller

    async def analyze(self, user_message: str, *, tenant_id, user_id) -> DraftSummary:
        prompt = _PROMPT.replace("{user_message}", user_message)
        raw = await self._call_with_fallback(prompt)
        return self._parse(raw)

    def _parse(self, raw: str) -> DraftSummary:
        # Strip code fences if LLM wraps JSON in markdown
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(l for l in lines if not l.startswith("```"))
        try:
            data = json.loads(text)
            return DraftSummary(**data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("IntentAnalyzer parse failed: %s; raw=%r", exc, raw[:200])
            # Fallback: derive from raw text
            return DraftSummary(
                intent_category="other",
                scope_summary=text[:80],
                key_constraints=[],
            )

    async def _call_with_fallback(self, prompt: str) -> str:
        try:
            return await self.llm_caller(prompt)
        except Exception as exc:
            logger.warning("Primary LLM failed (%s); using fallback", exc)
            if self.fallback_caller is None:
                raise
            return await self.fallback_caller(prompt)
