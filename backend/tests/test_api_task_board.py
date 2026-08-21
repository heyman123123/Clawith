"""T3.3: Task Board API surface."""
from app.api.task_board import router


def test_router_has_expected_endpoints():
    paths = sorted(r.path for r in router.routes if hasattr(r, "path"))
    expected = [
        "/api/task-board/cards",
        "/api/task-board/cards/{card_id}/move",
        "/api/task-board/cards/{card_id}/assign",
        "/api/task-board/cards/{card_id}/done",
    ]
    for e in expected:
        assert e in paths, f"missing endpoint: {e}"


def test_router_prefix_is_task_board():
    assert router.prefix == "/api/task-board"


def test_router_tag():
    assert "task-board" in router.tags
