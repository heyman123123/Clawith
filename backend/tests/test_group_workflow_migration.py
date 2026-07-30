"""Schema-level contracts for durable group workflows."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.group_workflow import GroupWorkflow, GroupWorkflowEvent


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/202607301500_add_group_workflows.py"
    spec = importlib.util.spec_from_file_location("group_workflow_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_scope_and_event_idempotency_are_durable_contracts() -> None:
    constraints = {item.name for item in GroupWorkflow.__table__.constraints}
    event_constraints = {item.name for item in GroupWorkflowEvent.__table__.constraints}

    assert GroupWorkflow.__tablename__ == "group_workflows"
    assert "uq_group_workflows_group" in constraints
    assert "uq_group_workflow_events_idempotency" in event_constraints


def test_migration_follows_ai_interaction_call_times() -> None:
    migration = _migration()

    assert migration.down_revision == "ai_interaction_times"
    assert migration.revision == "group_workflows"
