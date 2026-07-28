"""P2.1 — dispatch loop + step-result collector.

This module is the *driver* layer around ``scheduler_tools.dispatch_task_to_role``.
Where ``scheduler_tools`` is the typed contract the 项目调度官 Agent calls
via the tool-calling loop, ``dispatcher`` is the deterministic batch API the
Runtime (or tests) can use to push the workflow forward and to collect
outputs from executed steps.

Two entry points:

* :func:`run_dispatch_loop` — scan the ``workflow_run_steps`` rows of a
  workflow for entries whose ``status='pending'`` and whose ``depends_on``
  set is fully satisfied by already-succeeded steps, then dispatch each
  one in ``step_order`` ascending.  The function returns
  ``{dispatched_count, step_ids}`` so the Runtime can render progress.
* :func:`collect_step_result` — stamp an executed step's output excerpt /
  file path / token usage onto the row, then transition ``status`` to
  ``quality_checking`` when a downstream quality step is waiting, or
  ``succeeded`` when the step is terminal.

Both functions tolerate the lightweight ``SimpleNamespace`` stubs that the
test suite uses to stand in for SQLAlchemy rows, while still doing the
right thing against the real ``AsyncSession``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.services.ao.run_repository import (
    get_run_steps,
    get_step,
    has_quality_step,
    mark_step_status,
)
from app.services.ao.scheduler_tools import (
    AOIntegrationError,
    scheduler_tool_context,
)
from app.services.ao.scheduler_tools import (
    dispatch_task_to_role as _scheduler_dispatch_task_to_role,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Re-exported so ``app.services.ao.__init__`` can pull
# ``dispatch_task_to_role`` from the dispatcher module without losing
# the canonical implementation.  The Runtime always imports the symbol
# from ``scheduler_tools`` directly; this alias is here only because the
# package surface promises ``dispatcher.dispatch_task_to_role``.
dispatch_task_to_role = _scheduler_dispatch_task_to_role


# Status tokens we use when transitioning step rows. They mirror the
# ``workflow_run_steps.status`` CHECK constraint declared in
# ``202607271300_add_ao_workflow_runs`` and are intentionally strings
# rather than an Enum so dispatcher.py stays decoupled from any future
# schema migration that adds new states.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DISPATCHED = "running"  # alias used in spec text; same DB enum value
STATUS_QUALITY_CHECKING = "quality_checking"
STATUS_SUCCEEDED = "succeeded"


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Bundle the identifiers a single dispatch round needs in one place.

    P2.3 callers that don't want to wrap a call in
    :func:`scheduler_tools.scheduler_tool_context` can construct a
    ``DispatchContext`` and pass it explicitly.  ``db`` is required; the
    UUIDs are optional but ``workflow_id`` must be present when the
    dispatcher needs to resolve the workflow row.
    """

    db: Any
    workflow_id: uuid.UUID | None = None
    scheduler_agent_id: uuid.UUID | None = None
    creator_id: uuid.UUID | None = None


async def run_dispatch_loop(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    scheduler_agent_id: uuid.UUID | None = None,
    creator_id: uuid.UUID | None = None,
) -> dict:
    """Dispatch every pending ``WorkflowRunStep`` whose deps are satisfied.

    The function performs the scheduler Agent's main work in P2.1:

    1. Load all steps for the workflow via :func:`run_repository.get_run_steps`.
    2. Build a ``{step_key -> status}`` index so a step's ``depends_on``
       list can be resolved to current statuses without re-querying.
    3. For each ``status='pending'`` step whose dependencies are all in
       ``{'succeeded', 'skipped'}``, call
       :func:`scheduler_tools.dispatch_task_to_role` with the step's
       ``agent_id`` and ``task_summary``.
    4. Stamp ``status='running'`` + ``started_at`` so dashboards show the
       step as in-flight.
    5. Return ``{dispatched_count, step_ids}`` — the Runtime can use this
       to decide whether to keep looping or yield.

    Errors raised by :func:`dispatch_task_to_role` (wrapped in
    :class:`AOIntegrationError`) bubble up unchanged so the Runtime can
    surface them. The loop is best-effort: an exception in iteration N
    aborts the whole batch — partial dispatch is reported via the return
    value of iterations 0..N-1.
    """
    steps = await get_run_steps(db, workflow_id=workflow_id)
    if not steps:
        return {"dispatched_count": 0, "step_ids": [], "workflow_id": str(workflow_id)}

    status_index: dict[str, str] = {step.step_key: step.status for step in steps}
    completed: list[str] = []

    with scheduler_tool_context(
        db=db,
        workflow_id=workflow_id,
        actor_agent_id=scheduler_agent_id,
        user_id=creator_id,
    ):
        for step in steps:
            if step.status != STATUS_PENDING:
                continue
            if not _dependencies_satisfied(step.depends_on, status_index):
                continue
            agent_id = step.agent_id
            if agent_id is None:
                raise AOIntegrationError(
                    f"Step {step.id} ({step.step_key}) has no agent_id assigned; "
                    "cannot dispatch."
                )
            await _scheduler_dispatch_task_to_role(
                str(agent_id),
                step.task_summary or step.step_key,
                step.input_refs,
                expected_outputs=_acceptance_to_expected_outputs(step.acceptance_text),
                step_id=str(step.id),
            )
            await mark_step_status(
                db,
                step_id=step.id,
                status=STATUS_DISPATCHED,
                started_at=datetime.now(UTC),
            )
            status_index[step.step_key] = STATUS_DISPATCHED
            completed.append(str(step.id))

    logger.info(
        "[AODispatcher] workflow {} dispatched {} step(s): {}",
        workflow_id,
        len(completed),
        completed,
    )
    return {
        "dispatched_count": len(completed),
        "step_ids": completed,
        "workflow_id": str(workflow_id),
    }


async def collect_step_result(
    db: AsyncSession,
    *,
    step_id: uuid.UUID,
    output_excerpt: str,
    output_file: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    auto_quality: bool = False,
    quality_threshold: int | None = None,
) -> dict:
    """Persist a step's execution output and advance its lifecycle status.

    Behaviour:

    * Look up the step row; raise :class:`AOIntegrationError` if missing
      (the collector must never silently drop a result).
    * Stamp ``output_excerpt``, ``output_file``, ``input_tokens``,
      ``output_tokens`` and ``completed_at``.
    * Set ``status='quality_checking'`` when another step in the
      workflow is the quality reviewer, ``status='succeeded'`` when this
      step is terminal.
    * When ``auto_quality=True`` and a quality reviewer exists for this
      step, run the rule engine (:func:`run_quality_check`) and let it
      decide the final status (``succeeded`` / ``quality_retry`` /
      ``quality_failed``). The P2.2 default behaviour remains opt-in so
      older callers / tests are not affected.
    * Return ``{ok, step_id, status, output_excerpt, output_file,
      input_tokens, output_tokens, completed_at, quality?}`` so the
      Runtime can serialise the outcome into the tool-calling loop.

    ``output_file`` is optional: a step may legitimately finish with an
    in-memory excerpt only. Tests rely on this when asserting the
    collector does not fail on missing files.
    """
    row = await get_step(db, step_id=step_id)
    if row is None:
        raise AOIntegrationError(f"collect_step_result: step {step_id} not found.")

    now = datetime.now(UTC)
    row.output_excerpt = output_excerpt
    row.output_file = output_file
    if input_tokens is not None:
        row.input_tokens = input_tokens
    if output_tokens is not None:
        row.output_tokens = output_tokens
    row.completed_at = now
    row.updated_at = now

    has_quality = await has_quality_step(
        db, workflow_id=row.workflow_id, step_key=row.step_key
    )
    quality_summary: dict | None = None

    if has_quality and auto_quality:
        from app.services.ao.quality_engine import run_quality_check

        try:
            outcome = await run_quality_check(
                db,
                workflow_id=row.workflow_id,
                tenant_id=row.tenant_id,
                step_id=step_id,
                output_text=output_excerpt,
            )
        except Exception as exc:  # noqa: BLE001 — quality failure must not poison collection
            logger.warning(
                "[AODispatcher] quality check failed for step {}: {}", step_id, exc
            )
            row.status = STATUS_QUALITY_CHECKING
            await db.flush()
            quality_summary = {"ok": False, "error": str(exc)}
        else:
            row.status = outcome.next_status
            row.retry_count = outcome.retry_count
            row.quality_score = outcome.verdict.score
            row.quality_feedback = outcome.verdict.feedback
            await db.flush()
            quality_summary = {
                "ok": True,
                "score": outcome.verdict.score,
                "next_status": outcome.next_status,
                "retry_count": outcome.retry_count,
            }
            next_status = outcome.next_status
            return {
                "ok": True,
                "step_id": str(step_id),
                "workflow_id": str(row.workflow_id),
                "step_key": row.step_key,
                "status": next_status,
                "output_excerpt": output_excerpt,
                "output_file": output_file,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "completed_at": now.isoformat(),
                "quality": quality_summary,
            }

    if has_quality:
        next_status = STATUS_QUALITY_CHECKING
    else:
        next_status = STATUS_SUCCEEDED
    row.status = next_status
    await db.flush()

    logger.info(
        "[AODispatcher] step {} collected → status={}, output_excerpt_len={}, output_file={}",
        step_id,
        next_status,
        len(output_excerpt or ""),
        output_file,
    )
    return {
        "ok": True,
        "step_id": str(step_id),
        "workflow_id": str(row.workflow_id),
        "step_key": row.step_key,
        "status": next_status,
        "output_excerpt": output_excerpt,
        "output_file": output_file,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "completed_at": now.isoformat(),
        "quality": quality_summary,
    }


def _dependencies_satisfied(
    depends_on: list[str] | tuple[str, ...] | None,
    status_index: dict[str, str],
) -> bool:
    """Return ``True`` when every ``depends_on`` key has reached a terminal state.

    A step with an empty / ``None`` dependency list is always ready to
    run. We treat ``succeeded`` and ``skipped`` as terminal-friendly
    states because downstream nodes should not block on a step the user
    has chosen to skip.
    """
    if not depends_on:
        return True
    terminal = {"succeeded", "skipped"}
    return all(status_index.get(key) in terminal for key in depends_on)


def _acceptance_to_expected_outputs(acceptance_text: str | None) -> list[str] | None:
    """Best-effort split of an AO ``acceptance_text`` JSON array into a list.

    ``acceptance_text`` is authored by HR as a free-form string; P1.4
    sometimes packs an expected-output list into it as a JSON array. We
    recover the structured form here so ``dispatch_task_to_role`` can
    forward it. Anything that fails to parse falls back to a single-item
    list with the raw text, which keeps the dispatcher non-fatal.
    """
    if not acceptance_text:
        return None
    import json

    try:
        parsed = json.loads(acceptance_text)
    except (TypeError, ValueError):
        return [acceptance_text.strip()]
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return parsed
    return [acceptance_text.strip()]


# Re-export the helper functions ``run_dispatch_loop`` and
# ``collect_step_result`` expect from ``scheduler_tools`` so callers do
# not have to import the inner module twice.
__all__ = [
    "STATUS_DISPATCHED",
    "STATUS_PENDING",
    "STATUS_QUALITY_CHECKING",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "DispatchContext",
    "collect_step_result",
    "dispatch_task_to_role",
    "run_dispatch_loop",
]