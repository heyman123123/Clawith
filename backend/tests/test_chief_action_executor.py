"""T4.5: ActionExecutor executes plan; failures are isolated."""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from app.services.agent_runtime.orchestrator.action_executor import ActionExecutor
from app.services.agent_runtime.orchestrator.checkpoint_state import (
    ActionPlan, CardMoveAction, CardAssignAction, DispatchTaskAction,
)


def _make_executor(task_board=None, command_intake=None):
    return ActionExecutor(
        task_board_service=task_board,
        command_intake_service=command_intake,
    )


@pytest.mark.asyncio
async def test_executor_runs_with_no_services():
    """When services are None, executor still returns results (no-op)."""
    exe = _make_executor(task_board=None, command_intake=None)
    plan = ActionPlan(
        move_cards=[CardMoveAction(
            card_id=uuid.uuid4(), to_column="review", expected_version=0
        )],
        dispatch_tasks=[DispatchTaskAction(
            agent_role="writer", title="write blog", description="",
        )],
    )
    result = await exe.execute(
        plan=plan,
        group_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        chief_agent_id=uuid.uuid4(),
    )
    assert result["moves_attempted"] == 1
    assert result["moves_succeeded"] == 1  # no-op success
    assert result["dispatches_succeeded"] == 1


@pytest.mark.asyncio
async def test_executor_calls_task_board_for_each_move():
    """T4.5: each move_card is delegated to TaskBoardService."""
    mock_tb = MagicMock()
    mock_tb.move_card = AsyncMock()
    exe = _make_executor(task_board=mock_tb)

    cid = uuid.uuid4()
    plan = ActionPlan(
        move_cards=[CardMoveAction(card_id=cid, to_column="done", expected_version=3)]
    )
    result = await exe.execute(
        plan=plan,
        group_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        chief_agent_id=uuid.uuid4(),
    )
    mock_tb.move_card.assert_awaited_once()
    kwargs = mock_tb.move_card.await_args.kwargs
    assert kwargs["card_id"] == cid
    assert kwargs["expected_version"] == 3


@pytest.mark.asyncio
async def test_executor_calls_command_intake_for_dispatch():
    """T4.5: dispatch_tasks use RuntimeCommandIntake (C1)."""
    mock_ci = MagicMock()
    mock_ci.enqueue_agent_run = AsyncMock()
    exe = _make_executor(command_intake=mock_ci)

    plan = ActionPlan(
        dispatch_tasks=[DispatchTaskAction(
            agent_role="researcher", title="research X", description="do X",
        )]
    )
    result = await exe.execute(
        plan=plan,
        group_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        chief_agent_id=uuid.uuid4(),
    )
    mock_ci.enqueue_agent_run.assert_awaited_once()
    assert result["dispatches_succeeded"] == 1


@pytest.mark.asyncio
async def test_executor_isolates_failures():
    """A failing move doesn't abort other moves."""
    mock_tb = MagicMock()
    call_count = [0]
    async def sometimes_fail(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("first move fails")
    mock_tb.move_card = AsyncMock(side_effect=sometimes_fail)
    exe = _make_executor(task_board=mock_tb)

    moves = [
        CardMoveAction(card_id=uuid.uuid4(), to_column="review", expected_version=0),
        CardMoveAction(card_id=uuid.uuid4(), to_column="done", expected_version=0),
    ]
    plan = ActionPlan(move_cards=moves)
    result = await exe.execute(
        plan=plan,
        group_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        chief_agent_id=uuid.uuid4(),
    )
    assert result["moves_attempted"] == 2
    assert result["moves_succeeded"] == 1
    assert result["moves_failed"] == 1


@pytest.mark.asyncio
async def test_executor_emits_notify_user():
    plan = ActionPlan(notify_user="Hello from Chief")
    exe = _make_executor()
    result = await exe.execute(
        plan=plan,
        group_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        chief_agent_id=uuid.uuid4(),
    )
    assert result["notify_user"] == "Hello from Chief"
