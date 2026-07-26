"""Pure milestone progress and completion logic tests."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

from app.services.project_milestone_service import (
    derive_milestone_status,
    milestone_progress,
    milestone_task_progress,
    parse_milestones_payload,
)


def _milestone(*, status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), status=status)


def _task(*, milestone_id: uuid.UUID | None, status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(milestone_id=milestone_id, status=status)


def test_milestone_progress_counts_done_milestones() -> None:
    done = _milestone(status="done")
    pending = _milestone(status="pending")
    progress = milestone_progress([done, pending], [])
    assert progress == {"completed": 1, "total": 2, "percent": 50}


def test_milestone_task_progress_for_assigned_tasks() -> None:
    milestone = _milestone()
    tasks = [
        _task(milestone_id=milestone.id, status="done"),
        _task(milestone_id=milestone.id, status="doing"),
        _task(milestone_id=uuid.uuid4(), status="done"),
    ]
    assert milestone_task_progress(milestone, tasks) == {"completed": 1, "total": 2, "percent": 50}


def test_derive_milestone_status_without_tasks_stays_pending() -> None:
    milestone = _milestone(status="pending")
    assert derive_milestone_status(milestone, []) == "pending"


def test_derive_milestone_status_marks_done_when_all_tasks_done() -> None:
    milestone = _milestone(status="active")
    tasks = [
        _task(milestone_id=milestone.id, status="done"),
        _task(milestone_id=milestone.id, status="done"),
    ]
    assert derive_milestone_status(milestone, tasks) == "done"


def test_derive_milestone_status_marks_active_when_work_started() -> None:
    milestone = _milestone(status="pending")
    tasks = [_task(milestone_id=milestone.id, status="doing")]
    assert derive_milestone_status(milestone, tasks) == "active"


def test_parse_milestones_payload_extracts_json_block() -> None:
    detail = (
        "拆解完成。\n"
        '{"milestones":[{"title":"调研","order_index":0,"task_titles":["产品调研"]},'
        '{"title":"交付","order_index":1,"task_titles":["汇总交付"]}]}'
    )
    items = parse_milestones_payload(detail)
    assert len(items) == 2
    assert items[0]["title"] == "调研"
    assert items[1]["order_index"] == 1
