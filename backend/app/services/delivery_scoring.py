"""Delivery scoring helpers (P3 / P7).

需求 §4.11 + §8.3 mandate a 100-point two-dimension scoring rubric:

* **quality** (0..100) — quality officer's verdict, weight 0.6
* **coverage** (0..100) — delivery manager's scope checklist, weight 0.4
* **final** = round(0.6 * quality + 0.4 * coverage)
* **pass** = final >= pass_threshold (default 90)
* **max rounds** = 3 — after the third rejection the workflow
  emits a ``shareholder_decision`` review card and stops auto-looping.

The module is pure (no DB) so it can be unit tested without infrastructure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

# Weighting pulled into constants so the test suite can pin the values
# without re-deriving them from the spec.
QUALITY_WEIGHT: float = 0.6
COVERAGE_WEIGHT: float = 0.4
DEFAULT_PASS_THRESHOLD: int = 90
MAX_ROUNDS: int = 3


@dataclass(frozen=True)
class ScoringResult:
    """Immutable view of a delivery round verdict."""

    final_score: int
    pass_threshold: int
    passed: bool
    quality: float
    coverage: float
    round_no: int
    exhausted: bool
    """``True`` when this rejection was the last allowed attempt."""


def compute_final_score(
    *,
    quality: float,
    coverage: float,
    pass_threshold: int = DEFAULT_PASS_THRESHOLD,
    round_no: int = 1,
) -> ScoringResult:
    """Combine the two dimensions, clamp inputs to [0, 100], and decide pass / fail.

    Inputs that are ``None`` or out of bounds are clamped to ``0`` so a
    missing delivery manager verdict does not crash the loop.  The
    function is pure so it is safe to call from CLI scripts.
    """

    safe_quality = _clamp(quality)
    safe_coverage = _clamp(coverage)
    weighted = QUALITY_WEIGHT * safe_quality + COVERAGE_WEIGHT * safe_coverage
    final = round(weighted)
    return ScoringResult(
        final_score=final,
        pass_threshold=pass_threshold,
        passed=final >= pass_threshold,
        quality=safe_quality,
        coverage=safe_coverage,
        round_no=round_no,
        exhausted=(round_no >= MAX_ROUNDS) and (final < pass_threshold),
    )


def _clamp(value: float | None) -> float:
    """Defensive clamp — ``None`` becomes ``0``, out-of-range is capped."""
    if value is None:
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0:
        return 0.0
    if numeric > 100:
        return 100.0
    return numeric


def attempt_label(round_no: int) -> str:
    """Render the round badge used in group messages."""
    return f"第 {round_no}/{MAX_ROUNDS} 轮验收"


def new_round_no(previous: int | None) -> int:
    """Return the next round number (uncapped).

    Callers must reject ``> MAX_ROUNDS`` themselves (e.g. HTTP 409).  Capping
    here used to silently reuse round ``MAX_ROUNDS``, which let clients keep
    inserting after the third failure and never hit the escalation gate.
    """
    if previous is None:
        return 1
    return max(1, int(previous) + 1)


__all__ = [
    "COVERAGE_WEIGHT",
    "DEFAULT_PASS_THRESHOLD",
    "MAX_ROUNDS",
    "QUALITY_WEIGHT",
    "ScoringResult",
    "attempt_label",
    "compute_final_score",
    "new_round_no",
]


# ---------------------------------------------------------------------------
# Tenant-scoped review builder (tiny helper used by API endpoints).
# ---------------------------------------------------------------------------


def build_review_payload(
    *,
    workflow_id: uuid.UUID,
    kind: str,
    payload: dict,
    requester_user_id: uuid.UUID | None = None,
) -> dict:
    """Return a ready-to-insert dict for :class:`WorkflowHumanReview`."""
    return {
        "workflow_id": workflow_id,
        "kind": kind,
        "status": "open",
        "payload": payload,
        "requester_user_id": requester_user_id,
    }
