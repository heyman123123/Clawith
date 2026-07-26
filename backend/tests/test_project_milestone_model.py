"""ProjectMilestone model structure tests."""

from pathlib import Path


def test_project_milestone_model_fields() -> None:
    source = Path("app/models/project.py").read_text()
    assert "class ProjectMilestone(Base):" in source
    assert '__tablename__ = "project_milestones"' in source
    for field in (
        "workflow_id",
        "title",
        "description",
        "order_index",
        "status",
        "created_by_agent_id",
        "created_at",
        "completed_at",
    ):
        assert field in source


def test_task_model_has_milestone_id() -> None:
    source = Path("app/models/task.py").read_text()
    assert "milestone_id" in source
    assert "project_milestones.id" in source


def test_migration_adds_project_milestones() -> None:
    source = Path("alembic/versions/202607261400_add_project_milestones.py").read_text()
    assert 'revision: str = "add_project_milestones"' in source
    assert 'down_revision: str | None = "add_hr_review_board"' in source
    assert "project_milestones" in source
    assert "milestone_id" in source
    assert "daily_report_enabled" not in source
