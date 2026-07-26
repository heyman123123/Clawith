import uuid
from types import SimpleNamespace

from app.services.decision_sync_content import build_decision_sync_content
from app.services.project_decision_dag import (
    apply_cancelled_task_ids,
    collect_unblocked_tasks,
    dependencies_done,
)


def _task(task_id: uuid.UUID, *, status: str, deps: list[str] | None = None):
    return SimpleNamespace(
        id=task_id,
        title=f"task-{task_id.hex[:6]}",
        status=status,
        dependency_task_ids=deps or [],
        agent_id=uuid.uuid4(),
    )


def test_dependencies_done_requires_all_dependencies():
    done_id = uuid.uuid4()
    blocked_id = uuid.uuid4()
    tasks_by_id = {
        str(done_id): _task(done_id, status="done"),
        str(blocked_id): _task(blocked_id, status="blocked", deps=[str(done_id)]),
    }
    assert dependencies_done(tasks_by_id[str(blocked_id)], tasks_by_id) is True


def test_apply_cancelled_task_ids_marks_failed():
    task_id = uuid.uuid4()
    tasks_by_id = {str(task_id): _task(task_id, status="blocked")}
    changed = apply_cancelled_task_ids(tasks_by_id, [str(task_id)])
    assert changed == [tasks_by_id[str(task_id)].title]
    assert tasks_by_id[str(task_id)].status == "failed"


def test_collect_unblocked_tasks():
    dep_id = uuid.uuid4()
    ready_id = uuid.uuid4()
    tasks = [
        _task(dep_id, status="done"),
        _task(ready_id, status="blocked", deps=[str(dep_id)]),
    ]
    tasks_by_id = {str(task.id): task for task in tasks}
    ready = collect_unblocked_tasks(tasks, tasks_by_id)
    assert [task.id for task in ready] == [ready_id]


def test_build_decision_sync_content_includes_marker():
    record_id = uuid.uuid4()
    content = build_decision_sync_content(
        record_id=record_id,
        summary={
            "summary": "Proceed",
            "actions": [{"action": "Build", "owner_role": "Eng", "acceptance": "Demo"}],
            "risks": ["Scope creep"],
        },
    )
    assert f"<!--decision_sync:{record_id}-->" in content
    assert "📋 决策摘要：Proceed" in content
    assert "Build" in content
