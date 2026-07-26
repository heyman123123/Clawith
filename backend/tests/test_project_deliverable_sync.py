import uuid

from app.services.project_deliverable_sync import (
    deliverable_group_path,
    parse_workspace_artifact_ref,
)


def test_parse_workspace_artifact_ref():
    agent_id = uuid.uuid4()
    parsed = parse_workspace_artifact_ref(f"workspace://{agent_id}/docs/plan.md")
    assert parsed is not None
    assert parsed[0] == agent_id
    assert parsed[1] == "docs/plan.md"


def test_parse_workspace_artifact_ref_rejects_non_workspace():
    assert parse_workspace_artifact_ref("tool-result://abc") is None
    assert parse_workspace_artifact_ref("workspace://not-a-uuid/path") is None


def test_deliverable_group_path_nests_under_task():
    task_id = uuid.uuid4()
    assert deliverable_group_path(task_id=task_id, relative_path="docs/a.md") == (
        f"deliverables/{task_id}/docs/a.md"
    )
