"""Quality rule definitions for the AO integration (P2.2).

Rules are intentionally pure functions of the step output — no LLM judge, no
network. The P3 evolution engine can add an LLM-judge layer on top without
touching the public signature.

Adding a new rule:

* Implement it in :func:`evaluate_output` (or compose existing primitives).
* Add it to :data:`RULE_CATALOG` so admins can opt-in by name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class QualityRule:
    """Static description of one rule."""

    key: str
    description: str


@dataclass(frozen=True)
class RuleResult:
    rule: str
    ok: bool
    detail: str
    weight: int


@dataclass(frozen=True)
class QualityVerdict:
    score: int
    passed: bool
    feedback: str
    per_rule: tuple[RuleResult, ...]


RULE_CATALOG: tuple[QualityRule, ...] = (
    QualityRule("min_length", "正文不少于设定字数"),
    QualityRule("must_mention", "正文必须包含必现关键词"),
    QualityRule("no_placeholder", "正文不得包含 TODO / 待补 / ??? 等占位符"),
    QualityRule("structure", "JSON 结构任务须可被 json.loads"),
)


_PLACEHOLDER_TOKENS = ("TODO", "TBD", "???", "待补", "待完善", "占位", "PLACEHOLDER")
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


def _strip_code_fence(text: str) -> str:
    match = _JSON_FENCE.search(text or "")
    return match.group(1).strip() if match else (text or "").strip()


def _check_min_length(text: str, threshold: int) -> RuleResult:
    body = (text or "").strip()
    length = len(body)
    if length >= threshold:
        return RuleResult("min_length", True, f"{length} ≥ {threshold}", 30)
    return RuleResult("min_length", False, f"{length} < {threshold}", 30)


def _check_must_mention(text: str, keywords: Iterable[str]) -> RuleResult:
    body = (text or "").strip()
    if not keywords:
        return RuleResult("must_mention", True, "no keywords required", 20)
    missing = [kw for kw in keywords if kw and kw not in body]
    if not missing:
        return RuleResult("must_mention", True, f"mentions all {len(list(keywords))} keyword(s)", 20)
    return RuleResult("must_mention", False, f"missing: {missing}", 20)


def _check_no_placeholder(text: str) -> RuleResult:
    body = (text or "").strip()
    hits = [token for token in _PLACEHOLDER_TOKENS if token in body]
    if not hits:
        return RuleResult("no_placeholder", True, "no placeholder tokens", 20)
    return RuleResult("no_placeholder", False, f"found: {hits}", 20)


def _check_structure(text: str, *, requires_json: bool) -> RuleResult:
    if not requires_json:
        return RuleResult("structure", True, "not required", 30)
    import json

    candidate = _strip_code_fence(text)
    try:
        json.loads(candidate)
    except json.JSONDecodeError as exc:
        return RuleResult("structure", False, f"json decode failed: {exc.msg}", 30)
    return RuleResult("structure", True, "json parses cleanly", 30)


def evaluate_output(
    *,
    step_id: str,
    output_text: str,
    rules: dict | None = None,
) -> QualityVerdict:
    """Run the rules and return a verdict with score in ``[0, 100]``.

    ``rules`` overrides default thresholds:

    * ``min_length`` (int) — minimum character count
    * ``must_mention`` (list[str]) — keywords required in the output
    * ``no_placeholder`` (bool, default True) — fail if placeholder tokens appear
    * ``requires_json`` (bool, default False) — fail if output is not JSON-parseable
    * ``threshold`` (int, default 80) — score cutoff for ``passed``
    """
    overrides = dict(rules or {})
    min_length = int(overrides.get("min_length", 50))
    keywords = list(overrides.get("must_mention") or [step_id])
    require_no_placeholder = bool(overrides.get("no_placeholder", True))
    requires_json = bool(overrides.get("requires_json", False))
    threshold = int(overrides.get("threshold", 80))

    rule_results: list[RuleResult] = [
        _check_min_length(output_text, min_length),
        _check_must_mention(output_text, keywords),
    ]
    if require_no_placeholder:
        rule_results.append(_check_no_placeholder(output_text))
    rule_results.append(_check_structure(output_text, requires_json=requires_json))

    awarded = sum(r.weight for r in rule_results if r.ok)
    total = sum(r.weight for r in rule_results) or 1
    score = round(awarded * 100 / total)
    passed = score >= threshold
    feedback_lines = [f"- **{r.rule}**: {'✅' if r.ok else '❌'} {r.detail}" for r in rule_results]
    feedback_lines.append(f"\n**总评：{score} / 100**（阈值 {threshold}，{'通过' if passed else '不通过'}）")
    return QualityVerdict(
        score=score,
        passed=passed,
        feedback="\n".join(feedback_lines),
        per_rule=tuple(rule_results),
    )


__all__ = [
    "RULE_CATALOG",
    "QualityRule",
    "QualityVerdict",
    "RuleResult",
    "evaluate_output",
]