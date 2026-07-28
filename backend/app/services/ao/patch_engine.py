"""LLM-driven soul patch drafting (P4).

The :func:`draft_patch_from_signals` orchestration:

1. Loads the most recent quality signals for the agent from
   :mod:`evolution_signal_service`.
2. Asks the existing Clawith chat gateway (via :func:`chat_complete`)
   to extract a list of *rule additions* in a strict JSON schema.
3. Composes a new ``draft_soul_md`` by appending the rules to the
   current baseline. Network or schema failures fall back to a
   deterministic "no_op" draft so callers can still record an audit
   trail.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.models.agent import Agent
from app.services.ao import evolution_signal_service as signals
from app.services.ao.llm_judge import LLMError, _try_parse_json, chat_complete
from app.services.llm.model_resolution import active_agent_model_candidates
from app.services.llm.utils import get_model_api_key

_PATCH_SYSTEM_PROMPT = (
    "You are a prompt engineer extracting bullet-point rules from QA "
    "feedback. Each rule must be actionable, phrased in the second "
    "person, and limited to one sentence. Output JSON only matching "
    "this schema: {\"rules\": [{\"title\": \"...\", \"rule\": \"...\"}, "
    "\"rationale\": \"...\"}. Provide at most 6 rules."
)


@dataclass(slots=True)
class PatchDraft:
    patch_strategy: str
    rationale: str | None
    rule_additions: list[dict[str, Any]]
    draft_soul_md: str | None
    used_llm: bool
    error: str | None = None


def _default_no_op(rationale: str) -> PatchDraft:
    return PatchDraft(
        patch_strategy="no_op",
        rationale=rationale,
        rule_additions=[],
        draft_soul_md=None,
        used_llm=False,
    )


async def _resolve_model(db, agent: Agent | None):
    if agent is not None:
        candidates = await active_agent_model_candidates(db, agent)
        if candidates:
            return candidates[0]
    return None


def _merge_rules(baseline: str, rules: list[dict[str, Any]]) -> str:
    if not rules:
        return baseline
    lines = baseline.rstrip().splitlines()
    lines.append("")
    lines.append("## 自动演化规则")
    for rule in rules:
        title = rule.get("title") or rule.get("rule") or ""
        body = rule.get("rule") or ""
        if title and title != body:
            lines.append(f"- **{title}**: {body}")
        elif body:
            lines.append(f"- {body}")
    return "\n".join(lines)


def _build_messages(summaries: list[str]) -> list[dict[str, str]]:
    joined = "\n".join(f"- {line}" for line in summaries[:32] if line)
    user_payload = (
        "Recent QA feedback summaries for this agent:\n"
        f"{joined or '(no feedback yet)'}\n\n"
        "Extract at most 6 concrete rules the agent should obey to avoid "
        "repeating these issues."
    )
    return [
        {"role": "system", "content": _PATCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_payload},
    ]


async def draft_patch_from_signals(
    db,
    *,
    agent: Agent,
    baseline_soul_md: str,
    limit: int = 16,
) -> PatchDraft:
    """Return a :class:`PatchDraft` for ``agent`` from recent signals."""
    rows = await signals.recent_signals_for_agent(db, agent_id=agent.id, limit=limit)
    if not rows:
        return _default_no_op("No signals available — patch draft skipped.")

    summaries: list[str] = []
    for row in rows:
        if row.summary:
            summaries.append(row.summary)
        for reason in row.reasons or []:
            if reason:
                summaries.append(reason)

    model = await _resolve_model(db, agent)
    if model is None:
        return _default_no_op(
            "No LLM available — patch draft skipped (LLM unreachable)."
        )

    try:
        response = await chat_complete(
            provider=model.provider,
            api_key=get_model_api_key(model),
            model=model.model,
            messages=_build_messages(summaries),
            temperature=0.2,
            max_tokens=512,
            timeout=60.0,
        )
    except LLMError as exc:  # pragma: no cover - depends on SDK state
        logger.warning("[PatchEngine] chat failure for {}: {}", agent.id, exc)
        return _default_no_op(f"LLM call failed: {exc}")

    raw = _extract_text(response)
    parsed = _try_parse_json(raw) or {}
    rules_raw = parsed.get("rules") or []
    if not isinstance(rules_raw, list):
        rules = []
    else:
        rules = [
            {
                "title": str(item.get("title", "")),
                "rule": str(item.get("rule", "")),
            }
            for item in rules_raw
            if isinstance(item, dict)
            if str(item.get("rule", "")).strip()
        ][:6]
    rationale = str(parsed.get("rationale") or "")[:1000]
    draft_text = _merge_rules(baseline_soul_md, rules) if rules else None

    return PatchDraft(
        patch_strategy="append_rules" if rules else "no_op",
        rationale=rationale or None,
        rule_additions=rules,
        draft_soul_md=draft_text,
        used_llm=True,
    )


def _extract_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content)


_SENTENCE_RE = re.compile(r"(?<=[。.!?！？\n])\s+")


def _truncate(text: str, limit: int = 8000) -> str:
    if not text:
        return ""
    text = text[:limit]
    sentences = _SENTENCE_RE.split(text)
    return sentences[0] if sentences else text


async def generate_signal_summary(
    *,
    step_id: uuid.UUID,
    judge_payload: dict | None,
    verdict_score: int,
) -> str:
    """Produce a one-liner summary used by ``record_quality_signal``."""
    if judge_payload and judge_payload.get("judge_used"):
        comments = judge_payload.get("comments") or ""
        reasons = judge_payload.get("reasons") or []
        joined = "; ".join(reasons[:3])
        return f"step {step_id} judge={verdict_score} comments={_truncate(comments)} reasons={joined}"[:1000]
    return f"step {step_id} score={verdict_score} (rule only)"


__all__ = [
    "PatchDraft",
    "draft_patch_from_signals",
    "generate_signal_summary",
]
