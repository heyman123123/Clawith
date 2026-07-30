"""Pure helpers for OKR settings cadence / workflow event normalization."""

from __future__ import annotations

import uuid
from typing import Any

from app.models.okr import OKRSettings

DEFAULT_WORKFLOW_EVENTS = ("stage_completed", "workflow_completed")
KNOWN_WORKFLOW_EVENTS = frozenset(
    {"stage_activated", "stage_completed", "approval_required", "workflow_completed"}
)


def normalize_push_cadence(value: str | None) -> str:
    if value in {"calendar", "workflow", "both"}:
        return value
    return "both"


def normalize_workflow_events(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_WORKFLOW_EVENTS)
    out: list[str] = []
    for item in raw:
        key = str(item).strip()
        if key in KNOWN_WORKFLOW_EVENTS and key not in out:
            out.append(key)
    return out or list(DEFAULT_WORKFLOW_EVENTS)


def normalize_excluded_group_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        try:
            uuid.UUID(text)
        except ValueError:
            continue
        if text not in out:
            out.append(text)
    return out


def calendar_collection_active(settings: OKRSettings) -> bool:
    cadence = normalize_push_cadence(getattr(settings, "push_cadence", None))
    return bool(settings.enabled and settings.daily_report_enabled and cadence in {"calendar", "both"})


def workflow_push_active(settings: OKRSettings) -> bool:
    cadence = normalize_push_cadence(getattr(settings, "push_cadence", None))
    return bool(settings.enabled and cadence in {"workflow", "both"})


__all__ = [
    "DEFAULT_WORKFLOW_EVENTS",
    "KNOWN_WORKFLOW_EVENTS",
    "calendar_collection_active",
    "normalize_excluded_group_ids",
    "normalize_push_cadence",
    "normalize_workflow_events",
    "workflow_push_active",
]
