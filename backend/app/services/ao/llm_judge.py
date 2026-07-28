"""LLM-as-judge that augments the rule-based quality engine (P3).

Goals
-----
* Stay on the same chat path as the rest of Clawith — we resolve an
  ``LLMModel`` row (primary → fallback → tenant default) and call
  :func:`chat_complete`. No new SDK dependency, no separate gateway.
* Produce a deterministic score the quality engine can act on.
* Fall back to the rule engine (``evaluate_output``) instead of failing
  the whole step when the gateway is unreachable.

Protocol
--------
The judge asks the model for an OpenAI-style JSON ``choices[0].message.content``
with shape::

    {
      "score": 0-100,
      "passed": true|false,
      "comments": "...",
      "reasons": ["..."]
    }

We parse defensively. If the model returns text we try JSON extraction,
then fall back to score-by-coercion (a ``score=85`` text) before finally
returning ``rule_only_verdict`` so the quality engine never sees an
exception thrown by the LLM call.

The threshold for ``passed`` comes from the workflow's
``quality_threshold`` (or 80 by default). The judge function returns a
:class:`JudgeResult` so callers can decide whether to record a high-value
feedback event for the evolution engine.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.llm import LLMModel
from app.models.workflow_run import WorkflowRunStep
from app.services.llm.client import LLMError, chat_complete
from app.services.llm.model_resolution import active_agent_model_candidates
from app.services.llm.utils import get_model_api_key

from .quality_rules import QualityVerdict, evaluate_output

logger = logging.getLogger(__name__)


_JUDGE_SYSTEM_PROMPT = (
    "You are a strict QA reviewer for an AI workflow execution. "
    "Given the step's task, expected acceptance criteria, and the "
    "candidate output, decide whether the output is acceptable. "
    "Respond with JSON only — no prose — matching the schema:\n"
    '{"score": <integer 0-100>, "passed": <bool>, '
    '"comments": "<short reasoning>", "reasons": ["...", "..."]}.\n'
    "Score 100 = perfect; below the threshold = passed=false."
)


@dataclass(slots=True)
class JudgeResult:
    """Composite verdict from LLM judge + rule engine.

    The judge never blocks quality decisions — when the LLM is unavailable
    we still produce a ``rule_verdict`` so callers can continue. Callers
    should log when ``judge_used=False`` so SRE can see gateway issues.
    """

    passed: bool
    score: int
    judge_used: bool
    rule_verdict: QualityVerdict
    comments: str = ""
    reasons: list[str] = field(default_factory=list)
    raw_text: str = ""
    error: str | None = None

    def to_feedback_payload(self) -> dict[str, Any]:
        """Stable payload stored on WorkflowRunStep.quality_feedback / asset."""
        return {
            "passed": self.passed,
            "score": self.score,
            "judge_used": self.judge_used,
            "comments": self.comments,
            "reasons": self.reasons,
            "rule_score": self.rule_verdict.score,
            "rule_passed": self.rule_verdict.passed,
            "error": self.error,
        }


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction.

    LLMs occasionally wrap JSON in code fences. Strip those first, then try
    the strictest parse, then a brace-bounded regex fallback.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def _coerce_score(parsed: dict[str, Any] | None, fallback: int) -> int:
    if parsed and isinstance(parsed.get("score"), (int, float)):
        return max(0, min(100, int(parsed["score"])))
    return fallback


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "pass", "passed"}:
            return True
        if lowered in {"false", "no", "fail", "failed"}:
            return False
    return None


async def _resolve_model(
    db: AsyncSession,
    *,
    agent: Agent | None,
    tenant_id: uuid.UUID | None,
) -> LLMModel | None:
    """Pick a usable ``LLMModel`` row.

    Preference order:
      1. ``agent.primary_model_id`` then ``agent.fallback_model_id``.
      2. Tenant default (``Tenant.default_model_id``) — handled inside
         ``active_agent_model_candidates``.
      3. First enabled non-deleted model in the tenant pool.
    """
    if agent is not None:
        candidates = await active_agent_model_candidates(db, agent)
        if candidates:
            return candidates[0]

    if tenant_id is None:
        return None

    default_q = await db.execute(
        select(LLMModel).where(
            LLMModel.tenant_id == tenant_id,
            LLMModel.deleted_at.is_(None),
            LLMModel.enabled.is_(True),
        )
    )
    rows = list(default_q.scalars().all())
    if rows:
        return rows[0]
    return None


def _build_messages(
    *,
    task_summary: str,
    acceptance: str,
    output_excerpt: str,
) -> list[dict[str, str]]:
    user_payload = (
        "Task:\n"
        f"{task_summary or '(unspecified)'}\n\n"
        "Acceptance criteria:\n"
        f"{acceptance or '(none provided)'}\n\n"
        "Output:\n"
        f"{(output_excerpt or '').strip()[:8000]}\n"
    )
    return [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_payload},
    ]


async def evaluate_step_with_judge(
    db: AsyncSession,
    *,
    step: WorkflowRunStep,
    output_excerpt: str,
    quality_threshold: int = 80,
    rule_registry: dict[str, Any] | None = None,
    agent: Agent | None = None,
    workflow_id: uuid.UUID | None = None,
) -> JudgeResult:
    """Combine LLM judge + rule engine for one workflow step.

    Parameters
    ----------
    db:
        Async session used for model lookup.
    step:
        The ``WorkflowRunStep`` we are grading.
    output_excerpt:
        Subset of the candidate output. P3 keeps it short by default
        (~8k chars) so judge latency stays predictable.
    quality_threshold:
        Score cutoff for ``passed``; default mirrors the workflow default.
    rule_registry:
        Optional override for tests; falls back to module-level RULES.
    agent:
        Owner agent for model resolution. ``None`` is allowed and forces
        tenant-wide fallback resolution.
    workflow_id:
        Optional override of the workflow context (debug-only).
    """
    rule_verdict = evaluate_output(
        step_id=str(step.id),
        output_text=output_excerpt or "",
        rules=_safe_rules(step, rule_registry),
    )

    model = await _resolve_model(db, agent=agent, tenant_id=step.tenant_id)
    if model is None:
        logger.info(
            "ao.llm_judge.no_model tenant=%s step=%s",
            step.tenant_id,
            step.id,
        )
        return JudgeResult(
            passed=rule_verdict.passed,
            score=max(rule_verdict.score, 0),
            judge_used=False,
            rule_verdict=rule_verdict,
            comments="No LLM available — falling back to rule engine.",
            error="no_model",
        )

    messages = _build_messages(
        task_summary=step.task_summary or "",
        acceptance=step.acceptance_text or "",
        output_excerpt=output_excerpt,
    )

    try:
        response = await chat_complete(
            provider=model.provider,
            api_key=get_model_api_key(model),
            model=model.model,
            messages=messages,
            base_url=model.base_url,
            temperature=0.0,
            max_tokens=512,
            timeout=60.0,
        )
    except LLMError as exc:
        logger.warning(
            "ao.llm_judge.call_failed tenant=%s step=%s err=%s",
            step.tenant_id,
            step.id,
            exc,
        )
        return JudgeResult(
            passed=rule_verdict.passed,
            score=max(rule_verdict.score, 0),
            judge_used=False,
            rule_verdict=rule_verdict,
            comments="LLM judge unavailable — rule engine only.",
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - defensive belt-and-braces
        logger.warning(
            "ao.llm_judge.unexpected_fail tenant=%s step=%s err=%s",
            step.tenant_id,
            step.id,
            exc,
        )
        return JudgeResult(
            passed=rule_verdict.passed,
            score=max(rule_verdict.score, 0),
            judge_used=False,
            rule_verdict=rule_verdict,
            comments="LLM judge raised — rule engine only.",
            error=str(exc),
        )

    raw_text = _extract_text(response)
    parsed = _try_parse_json(raw_text)
    score = _coerce_score(parsed, fallback=rule_verdict.score)
    parsed_pass = _coerce_bool(parsed.get("passed")) if parsed else None
    passed = (
        parsed_pass
        if parsed_pass is not None
        else (rule_verdict.passed and score >= quality_threshold)
    )
    comments = (parsed.get("comments") if parsed else "") or ""
    reasons_raw = parsed.get("reasons") if parsed else None
    reasons = [str(r) for r in reasons_raw] if isinstance(reasons_raw, list) else []

    return JudgeResult(
        passed=bool(passed),
        score=score,
        judge_used=True,
        rule_verdict=rule_verdict,
        comments=str(comments),
        reasons=reasons,
        raw_text=raw_text,
    )


def _extract_text(response: Any) -> str:
    """OpenAI-compatible chat response → assistant text."""
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    message = first.get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        chunks = []
        for chunk in content:
            if isinstance(chunk, dict):
                chunks.append(str(chunk.get("text", "")))
            else:
                chunks.append(str(chunk))
        return "\n".join(chunks)
    return str(content)


def _safe_rules(
    step: WorkflowRunStep,
    rule_registry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pull rules from the step's acceptance text + caller override."""
    if rule_registry is not None:
        return rule_registry
    return _rules_from_acceptance(step.acceptance_text or "")


def _rules_from_acceptance(acceptance: str) -> dict[str, Any]:
    """Derive ``min_length`` / ``must_mention`` from acceptance text.

    P3 keeps it conservative: if the acceptance text mentions a number
    we treat it as the minimum length; otherwise we only enforce the
    default 200-char minimum defined by the rule engine.
    """
    rules: dict[str, Any] = {}
    if not acceptance:
        return rules
    match = re.search(r"(\d{2,5})\s*(?:字|words?|chars?)", acceptance, re.IGNORECASE)
    if match:
        rules["min_length"] = int(match.group(1))
    if "JSON" in acceptance or "json" in acceptance:
        rules["structure"] = "json"
    return rules


__all__ = [
    "JudgeResult",
    "evaluate_step_with_judge",
]
