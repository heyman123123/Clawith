"""start_chief_run command handler.

Called by CommandWorker when a 'start_chief_run' command is dequeued.
Spins up an OrchestratorRunLoop as a background asyncio task.

Integration: the operator must wire this handler into the existing
CommandWorker dispatch table (see deployment docs). It is NOT auto-wired.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from app.services.agent_runtime.orchestrator.orchestrator_run import OrchestratorRunLoop
from app.services.agent_runtime.orchestrator.event_listener import EventListener
from app.services.agent_runtime.orchestrator.reasoner import Reasoner
from app.services.agent_runtime.orchestrator.action_executor import ActionExecutor

logger = logging.getLogger(__name__)

# Global registry of running Chief Run loops (per group_id)
_running_loops: dict[uuid.UUID, asyncio.Task] = {}


async def handle_start_chief_run(command: dict) -> None:
    """Spawn a Chief Run loop for the given group.

    Args:
        command: {
            tenant_id: str,
            chief_run_id: str,
            group_id: str,
            chief_agent_id: str,
            runtime_thread_id: str,
            reasoner: optional Reasoner (with real LLM caller),
            action_executor: optional ActionExecutor (with real services),
        }
    """
    group_id = uuid.UUID(command["group_id"])
    if group_id in _running_loops and not _running_loops[group_id].done():
        logger.info("Chief Run already active for group %s; skipping", group_id)
        return

    reasoner = command.get("reasoner") or Reasoner(llm_caller=_default_llm_caller)
    action_executor = command.get("action_executor") or ActionExecutor()
    event_listener = EventListener()

    loop = OrchestratorRunLoop(
        event_listener=event_listener,
        reasoner=reasoner,
        action_executor=action_executor,
    )
    task = asyncio.create_task(
        loop.run(group_id=group_id, tenant_id=uuid.UUID(command["tenant_id"]))
    )
    _running_loops[group_id] = task
    logger.info("Chief Run started for group %s (task=%s)", group_id, task.get_name())


async def _default_llm_caller(prompt: str) -> str:
    """Stub LLM caller for development; replace with real one in deployment."""
    raise NotImplementedError(
        "Wire a real LLM caller (e.g. app.services.llm.gateway) into the "
        "handle_start_chief_run command's 'reasoner' kwarg."
    )


def get_running_loops() -> dict[uuid.UUID, asyncio.Task]:
    """Test/observability hook."""
    return dict(_running_loops)
