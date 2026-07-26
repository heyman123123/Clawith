"""Create and finalize structured decision records."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import DecisionRecord
from app.models.project import ProjectWorkflow

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_DECISION_SUMMARY_KEY = re.compile(r'"decision_summary"\s*:\s*(\{.*?\})', re.S)


def extract_decision_summary(text: str) -> dict[str, Any] | None:
    """Extract a decision_summary JSON object from assistant text."""
    if not text or not text.strip():
        return None
    for pattern in (_JSON_FENCE, _DECISION_SUMMARY_KEY):
        match = pattern.search(text)
        if match is None:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "decision_summary" in payload and isinstance(payload["decision_summary"], dict):
            payload = payload["decision_summary"]
        if isinstance(payload, dict):
            return payload
    return None


def validate_decision_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Ensure required decision_summary keys exist with sane defaults."""
    normalized = dict(summary)
    summary_value = normalized.get("summary")
    if isinstance(summary_value, list):
        normalized["summary"] = "\n".join(str(item) for item in summary_value)
    elif summary_value is None:
        normalized["summary"] = ""
    else:
        normalized["summary"] = str(summary_value)
    normalized["actions"] = list(normalized.get("actions") or [])
    normalized["risks"] = list(normalized.get("risks") or [])
    normalized["cancelled_tasks"] = list(normalized.get("cancelled_tasks") or [])
    normalized["new_tasks"] = list(normalized.get("new_tasks") or [])
    return normalized


async def create_decision_record_from_summary(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    decision_group_id: uuid.UUID,
    decision_session_id: uuid.UUID,
    project_group_id: uuid.UUID,
    project_session_id: uuid.UUID,
    decision_summary: dict[str, Any],
    participants: list[Any],
) -> DecisionRecord:
    record = DecisionRecord(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        decision_group_id=decision_group_id,
        decision_session_id=decision_session_id,
        project_group_id=project_group_id,
        project_session_id=project_session_id,
        decision_summary=validate_decision_summary(decision_summary),
        participants=participants,
        status="dispatched",
    )
    db.add(record)
    await db.flush()
    return record


async def finalize_decision_record(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    decision_session_id: uuid.UUID,
    project_session_id: uuid.UUID,
    decision_summary: dict[str, Any],
    participants: list[Any],
) -> DecisionRecord:
    """Persist a decision record and dispatch it to the project leader."""
    if workflow.decision_group_id is None or workflow.group_id is None:
        raise ValueError("Project workflow is missing decision or execution group")

    record = await create_decision_record_from_summary(
        db,
        workflow_id=workflow.id,
        decision_group_id=workflow.decision_group_id,
        decision_session_id=decision_session_id,
        project_group_id=workflow.group_id,
        project_session_id=project_session_id,
        decision_summary=decision_summary,
        participants=participants,
    )
    from app.services.project_decision_dispatcher import dispatch_decision_to_project_leader

    await dispatch_decision_to_project_leader(db, record_id=record.id)
    return record
