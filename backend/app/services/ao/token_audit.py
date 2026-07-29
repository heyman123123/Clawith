"""Step-level token audit helpers (需求 §4.8 / §8.6).

``collect_step_result`` already stamps ``WorkflowRunStep.input_tokens`` /
``output_tokens``.  This module rolls those values up onto
``ProjectWorkflow.total_input_tokens`` / ``total_output_tokens`` so the
metrics dashboard and growth centre can read a single workflow-level
summary without re-aggregating every time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import ProjectWorkflow
from app.models.workflow_run import WorkflowRunStep


@dataclass(frozen=True, slots=True)
class TokenTotals:
    workflow_id: uuid.UUID
    total_input_tokens: int
    total_output_tokens: int
    step_count_with_usage: int


def _safe_int(value: int | None) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def apply_step_token_usage(
    db: AsyncSession,
    *,
    step: WorkflowRunStep,
    input_tokens: int | None,
    output_tokens: int | None,
) -> TokenTotals:
    """Stamp step tokens (if provided) and recompute workflow totals from all steps.

    Idempotent with respect to the latest stamped values: totals are always
    derived from ``SUM(workflow_run_steps.*)`` rather than incremental
    deltas, so re-collecting a step does not double-count.
    """
    if input_tokens is not None:
        step.input_tokens = _safe_int(input_tokens)
    if output_tokens is not None:
        step.output_tokens = _safe_int(output_tokens)
    await db.flush()
    return await recompute_workflow_token_totals(db, workflow_id=step.workflow_id)


async def recompute_workflow_token_totals(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
) -> TokenTotals:
    """Recompute and persist ``ProjectWorkflow`` token counters from step rows."""
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(WorkflowRunStep.input_tokens), 0),
                func.coalesce(func.sum(WorkflowRunStep.output_tokens), 0),
                func.count(WorkflowRunStep.id).filter(
                    (WorkflowRunStep.input_tokens.is_not(None))
                    | (WorkflowRunStep.output_tokens.is_not(None))
                ),
            ).where(WorkflowRunStep.workflow_id == workflow_id)
        )
    ).one()
    total_in = int(row[0] or 0)
    total_out = int(row[1] or 0)
    used_steps = int(row[2] or 0)

    workflow = await db.get(ProjectWorkflow, workflow_id)
    if workflow is not None:
        workflow.total_input_tokens = total_in
        workflow.total_output_tokens = total_out
        await db.flush()
        logger.debug(
            "[TokenAudit] workflow={} totals in={} out={} steps_with_usage={}",
            workflow_id,
            total_in,
            total_out,
            used_steps,
        )

    return TokenTotals(
        workflow_id=workflow_id,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        step_count_with_usage=used_steps,
    )


async def get_workflow_token_report(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    """Return a JSON-friendly usage report for one workflow (tenant-scoped)."""
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
        )
    )
    if workflow is None:
        return {"ok": False, "error": "workflow_not_found"}

    steps = (
        await db.scalars(
            select(WorkflowRunStep)
            .where(
                WorkflowRunStep.workflow_id == workflow_id,
                WorkflowRunStep.tenant_id == tenant_id,
            )
            .order_by(WorkflowRunStep.step_order.asc())
        )
    ).all()

    return {
        "ok": True,
        "workflow_id": str(workflow_id),
        "total_input_tokens": int(workflow.total_input_tokens or 0),
        "total_output_tokens": int(workflow.total_output_tokens or 0),
        "steps": [
            {
                "step_id": str(s.id),
                "step_key": s.step_key,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "status": s.status,
            }
            for s in steps
        ],
    }


__all__ = [
    "TokenTotals",
    "apply_step_token_usage",
    "get_workflow_token_report",
    "recompute_workflow_token_totals",
]
