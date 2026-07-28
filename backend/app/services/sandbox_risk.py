"""Pure helpers for classifying sandbox runs + routing human approvals.

Designed to be testable without a database. Production code calls these
from :mod:`app.services.skill_market_service`. The thresholds here are
intentionally coarse: ``high`` triggers human approval, ``medium`` only
flags the listing for review but does not block auto-publish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RiskAssessment:
    """The verdict of the static + dynamic risk heuristics."""

    risk_level: str
    detected_patterns: tuple[str, ...]
    requires_human_review: bool
    rationale: str

    def to_dict(self) -> dict:
        return {
            "risk_level": self.risk_level,
            "detected_patterns": list(self.detected_patterns),
            "requires_human_review": self.requires_human_review,
            "rationale": self.rationale,
        }


# Patterns that always escalate regardless of sandbox policy.
# We deliberately keep this list conservative — anything that looks like
# it could touch shell / fs / network / persistence is flagged "high".
_HIGH_PATTERNS: tuple[tuple[str, str], ...] = (
    ("shell_exec", r"\b(shutil\.rmtree|os\.system|os\.popen|subprocess\.[A-Za-z]+)\b"),
    ("fs_root", r"\b(chmod|chown)\b\s+(?:-R\s+)?[/~]"),
    ("network", r"\b(socket\.|urllib\.|requests\.|http\.client\.|ftplib\.|smtplib\.)\b"),
    ("process_kill", r"\b(os\.kill|signal\.|killpg)\b"),
    ("privilege_esc", r"\b(sudo|chroot|setuid|setgid)\b"),
    ("credential_read", r"(?i)\b(env\[|os\.environ\[|getenv\().*(SECRET|TOKEN|PASSWORD|KEY)\b"),
    ("data_exfil", r"\b(__import__|globals\(\)|locals\(\))\b"),
    ("destructive_shell", r"\b(rm -rf|mkfs|dd if=|:(){ :)"),
)

_MEDIUM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("subprocess_safe", r"\basync_subprocess\b"),
    ("fs_write_workspace", r"\b(open\([^)]*[\"']\w)"),
    ("dynamic_eval", r"\b(eval\(|exec\(|compile\()"),
    ("importlib", r"\b(importlib|__import__)\b"),
)


def _scan(code: str, patterns: Iterable[tuple[str, str]]) -> list[str]:
    import re

    out: list[str] = []
    for label, pattern in patterns:
        if re.search(pattern, code, flags=re.MULTILINE):
            out.append(label)
    return out


def assess_risk(code: str, *, allow_network: bool = False) -> RiskAssessment:
    """Inspect ``code`` and return a :class:`RiskAssessment`.

    The verdict is independent of actual sandbox execution — it is the
    "static" layer that catches obvious safety smells before any
    subprocess is launched. When ``allow_network`` is ``True`` the
    network patterns are downgraded from "high" to "medium" because
    operators explicitly opted in.
    """

    import re

    matches = _scan(code, _HIGH_PATTERNS)
    rationale_bits: list[str] = []

    if matches:
        # If allow_network is set and only "network" pattern matched,
        # downgrade to medium.
        if allow_network and matches == ["network"]:
            risk_level = "medium"
            rationale_bits.append("network access explicitly enabled")
        else:
            risk_level = "high"
            rationale_bits.append(f"high-risk patterns: {', '.join(matches)}")
        return RiskAssessment(
            risk_level=risk_level,
            detected_patterns=tuple(matches),
            requires_human_review=(risk_level == "high"),
            rationale="; ".join(rationale_bits) or "high-risk patterns detected",
        )

    medium_matches = _scan(code, _MEDIUM_PATTERNS)
    if medium_matches:
        return RiskAssessment(
            risk_level="medium",
            detected_patterns=tuple(medium_matches),
            requires_human_review=False,
            rationale=f"medium-risk patterns: {', '.join(medium_matches)}",
        )

    # Defensive sanity: blank code or comments only.
    if not re.search(r"\S", code):
        return RiskAssessment(
            risk_level="low",
            detected_patterns=(),
            requires_human_review=False,
            rationale="empty code",
        )
    return RiskAssessment(
        risk_level="low",
        detected_patterns=(),
        requires_human_review=False,
        rationale="no risky patterns detected",
    )


def combine_risk(*assessments: RiskAssessment) -> RiskAssessment:
    """Combine multiple risk verdicts (e.g. baseline + delta + caller hints)."""

    if not assessments:
        return RiskAssessment(
            risk_level="low",
            detected_patterns=(),
            requires_human_review=False,
            rationale="no assessments",
        )
    levels = [a.risk_level for a in assessments]
    if "high" in levels:
        winner = "high"
    elif "medium" in levels:
        winner = "medium"
    else:
        winner = "low"
    patterns: list[str] = []
    for a in assessments:
        patterns.extend(a.detected_patterns)
    return RiskAssessment(
        risk_level=winner,
        detected_patterns=tuple(dict.fromkeys(patterns)),
        requires_human_review=winner == "high",
        rationale="combined verdict: " + ", ".join(a.rationale for a in assessments),
    )


def should_auto_publish(risk_level: str) -> bool:
    """Policy: only low-risk listings auto-publish. Anything above needs review."""
    return risk_level == "low"


__all__ = [
    "RiskAssessment",
    "assess_risk",
    "combine_risk",
    "should_auto_publish",
]
