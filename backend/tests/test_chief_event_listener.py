"""T4.2: EventListener compensation poll + queue plumbing."""
import asyncio
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from app.services.agent_runtime.orchestrator.event_listener import EventListener, OrchestratorEvent


def test_event_listener_constructs_with_default_interval():
    el = EventListener()
    assert el.compensation_interval == 30


def test_event_listener_compensation_interval_customizable():
    el = EventListener(compensation_interval_seconds=5)
    assert el.compensation_interval == 5


def test_stop_signal_can_be_set():
    el = EventListener()
    assert not el._stop.is_set()
    el.stop()
    assert el._stop.is_set()


def test_orchestrator_event_holds_all_fields():
    e = OrchestratorEvent(
        event_id=uuid.uuid4(),
        event_type="card_moved",
        task_card_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        actor_id=uuid.uuid4(),
        actor_type="chief",
        payload={"from": "review", "to": "done"},
        occurred_at=None,
    )
    assert e.event_type == "card_moved"
    assert e.payload == {"from": "review", "to": "done"}
