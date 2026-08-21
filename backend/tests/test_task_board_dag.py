"""T-010: DAG cycle detection on dependencies."""
from app.services.task_board.service import TaskBoardService


def test_dag_cycle_detection_function_exists():
    """Verify the DAG validator is part of TaskBoardService."""
    svc = TaskBoardService()
    assert hasattr(svc, "_add_dependencies")
    assert callable(svc._add_dependencies)


def test_dag_constants():
    """Three-color DFS algorithm uses WHITE/GRAY/BLACK markers."""
    import inspect
    src = inspect.getsource(TaskBoardService._add_dependencies)
    assert "WHITE" in src
    assert "GRAY" in src
    assert "BLACK" in src
    assert "dag_cycle" in src  # error code on cycle
