"""T4.7 + T-009: Deadlock avoidance mechanism."""
from unittest.mock import MagicMock
import pytest
from app.services.agent_runtime.orchestrator.orchestrator_run import OrchestratorRunLoop


def test_failure_threshold_is_5():
    assert OrchestratorRunLoop.FAILURE_THRESHOLD == 5


def test_cooldown_is_5_minutes():
    assert OrchestratorRunLoop.COOLDOWN_SECONDS == 300


def test_has_degraded_marker():
    loop = OrchestratorRunLoop(
        event_listener=MagicMock(), reasoner=MagicMock(), action_executor=MagicMock()
    )
    assert hasattr(loop, "_mark_degraded")
    assert hasattr(loop, "_bump_failure")
    assert hasattr(loop, "_reset_failures")


def test_failure_methods_are_async():
    import inspect
    assert inspect.iscoroutinefunction(OrchestratorRunLoop._bump_failure)
    assert inspect.iscoroutinefunction(OrchestratorRunLoop._reset_failures)
    assert inspect.iscoroutinefunction(OrchestratorRunLoop._mark_degraded)
