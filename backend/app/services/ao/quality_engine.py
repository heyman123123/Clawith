"""Quality engine — P2.2 MVP.

This is the rule-based evaluator the scheduler and ``dispatcher`` rely on to
gate step progression. The verdict produced here drives:

1. ``workflow_run_steps`` status transitions:

   * ``score >= threshold`` → ``succeeded`` (or ``awaiting_next_step`` if you
     prefer — for now we land on ``succeeded`` and let downstream steps
     advance naturally).
   * score below threshold and ``retry_count < max_retries`` → ``quality_retry``.
   * otherwise → ``quality_failed``.

2. Persisted feedback file under ``02-质控/feedback/step_<id>.md`` via
   :func:`app.services.ao.asset_writer.write_step_asset`.

3. A best-effort :func:`run_quality_check` hook returning a JSON-friendly
   dict so the Runtime can echo the verdict back into the group feed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from app.models.agent import Agent as AgentModel
from app.models.project import ProjectWorkflow
from app.models.workflow_run import WorkflowRunStep
from app.services.ao.asset_writer import write_step_asset
from app.services.ao.evolution_engine import (
    record_quality_step_passed,
    seed_role_baseline,
)
from app.services.ao.llm_judge import evaluate_step_with_judge
from app.services.ao.quality_rules import QualityVerdict, evaluate_output

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


TERMINAL_PASS_STATUS = "succeeded"
RETRY_STATUS = "quality_retry"
FAIL_STATUS = "quality_failed"
DEFAULT_THRESHOLD = 80
DEFAULT_MAX_RETRIES = 2


@dataclass(frozen=True)
class QualityOutcome:
    verdict: QualityVerdict
    next_status: str
    retry_count: int
    feedback_asset: dict | None


async def _load_step(db: AsyncSession, step_id: uuid.UUID) -> WorkflowRunStep | None:
    return await db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))


async def _load_workflow(db: AsyncSession, workflow_id: uuid.UUID) -> ProjectWorkflow | None:
    return await db.scalar(select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id))


def _resolve_rules(workflow: ProjectWorkflow | None, step: WorkflowRunStep) -> dict:
    base: dict = {}
    if workflow is not None and workflow.quality_threshold:
        base["threshold"] = int(workflow.quality_threshold)
    step_rules = (step.acceptance_text or "").strip()
    if step_rules.startswith("{") and step_rules.endswith("}"):
        import json

        try:
            base.update(json.loads(step_rules))
        except json.JSONDecodeError:
            pass
    return base


async def run_quality_check(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    step_id: uuid.UUID,
    output_text: str | None = None,
    enable_llm_judge: bool = True,
) -> QualityOutcome:
    """Run rule-based evaluation and persist the verdict + asset side effect.

    When ``enable_llm_judge`` is ``True`` (the default) we layer the
    :func:`app.services.ao.llm_judge.evaluate_step_with_judge` over the
    rule engine. The judge's verdict replaces the rule score so the
    rest of this function — retry logic, asset writing, status
    transitions — keeps working unchanged. When the LLM gateway is
    unreachable the judge returns the rule verdict as a fallback so we
    never raise.
    """
    step = await _load_step(db, step_id)
    if step is None:
        raise ValueError(f"WorkflowRunStep {step_id} not found")
    workflow = await _load_workflow(db, workflow_id)
    rules = _resolve_rules(workflow, step)
    text = output_text if output_text is not None else (step.output_excerpt or "")

    rule_verdict = evaluate_output(step_id=str(step_id), output_text=text, rules=rules)
    threshold = int(rules.get("threshold", DEFAULT_THRESHOLD))
    verdict, judge_payload = await _maybe_run_judge(
        db,
        step=step,
        output_text=text,
        rule_verdict=rule_verdict,
        threshold=threshold,
        enabled=enable_llm_judge,
    )
    max_retries = int(rules.get("max_retries", DEFAULT_MAX_RETRIES))
    new_retry_count = int(step.retry_count or 0)

    if verdict.score >= threshold:
        next_status = TERMINAL_PASS_STATUS
    elif new_retry_count < max_retries:
        next_status = RETRY_STATUS
        new_retry_count += 1
    else:
        next_status = FAIL_STATUS

    step.quality_score = verdict.score
    step.quality_feedback = verdict.feedback
    step.status = next_status
    step.retry_count = new_retry_count
    if next_status in {TERMINAL_PASS_STATUS, FAIL_STATUS}:
        from datetime import UTC, datetime

        step.completed_at = datetime.now(UTC)
    await db.flush()

    asset_result: dict | None = None
    try:
        body = (
            f"# 质控反馈\n\n"
            f"- 步骤 ID：{step_id}\n"
            f"- 评分：{verdict.score}\n"
            f"- 状态：{next_status}\n"
        )
        if judge_payload:
            body += (
                f"- LLM judge：{'启用' if judge_payload.get('judge_used') else '降级为规则'}\n"
                f"- 评论：{judge_payload.get('comments', '')[:500]}\n"
            )
        body += f"\n## 反馈\n\n{verdict.feedback}\n"
        asset_result = await write_step_asset(
            db,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            step_id=step_id,
            category="quality",
            subdir="feedback",
            filename=f"step_{step_id}.md",
            content=body,
            metadata={
                "score": verdict.score,
                "status": next_status,
                "judge": judge_payload,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[QualityEngine] feedback asset failed for {}: {}", step_id, exc)

    if workflow is not None:
        if next_status == TERMINAL_PASS_STATUS:
            workflow.last_event_at = step.completed_at
        await db.flush()

    if (
        next_status == TERMINAL_PASS_STATUS
        and step.agent_id is not None
    ):
        try:
            await _record_evolution_signal(
                db,
                step=step,
                verdict=verdict,
                judge_payload=judge_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[QualityEngine] evolution signal failed for step={}: {}",
                step_id,
                exc,
            )

    return QualityOutcome(
        verdict=verdict,
        next_status=next_status,
        retry_count=new_retry_count,
        feedback_asset=asset_result,
    )


async def _maybe_run_judge(
    db: AsyncSession,
    *,
    step: WorkflowRunStep,
    output_text: str,
    rule_verdict: QualityVerdict,
    threshold: int,
    enabled: bool,
) -> tuple[QualityVerdict, dict | None]:
    """Best-effort LLM judge wrapper.

    Returns the verified ``QualityVerdict`` and a payload dict for asset
    metadata. The payload is ``None`` when the judge was disabled so the
    existing rule-only behaviour remains identical to pre-P3.
    """
    if not enabled:
        return rule_verdict, None

    agent = await db.scalar(select(AgentModel).where(AgentModel.id == step.agent_id)) if step.agent_id else None
    judge_result = await evaluate_step_with_judge(
        db,
        step=step,
        output_excerpt=output_text,
        quality_threshold=threshold,
        agent=agent,
    )
    if not judge_result.judge_used:
        payload = {
            "judge_used": False,
            "score": judge_result.score,
            "passed": judge_result.passed,
            "error": judge_result.error,
            "comments": judge_result.comments,
        }
    else:
        payload = judge_result.to_feedback_payload()

    judge_verdict = QualityVerdict(
        score=judge_result.score,
        passed=judge_result.passed,
        feedback=rule_verdict.feedback
        + (
            "\n\n## LLM judge\n"
            + f"- 评论：{judge_result.comments or '(无)'}\n"
            + f"- 触发：{'已用LLM' if judge_result.judge_used else '降级为规则'}\n"
            + (
                f"- 原因：{'、'.join(judge_result.reasons) or '—'}\n"
                if judge_result.reasons
                else ""
            )
        ),
        per_rule=rule_verdict.per_rule,
    )
    return judge_verdict, payload


async def _record_evolution_signal(
    db: AsyncSession,
    *,
    step: WorkflowRunStep,
    verdict: QualityVerdict,
    judge_payload: dict | None,
) -> None:
    """Update the running version's quality score after a step passes.

    Pulled out of :func:`run_quality_check` so the judge rollout can be
    skipped cleanly when ``enable_llm_judge`` is False.
    """

    agent = (
        await db.scalar(select(AgentModel).where(AgentModel.id == step.agent_id))
        if step.agent_id is not None
        else None
    )
    if agent is None:
        return

    soul_md = step.output_file or ""
    if not soul_md:
        baseline = await seed_role_baseline(
            db,
            agent=agent,
            soul_md=step.output_excerpt or f"step-{step.id}",
            summary=f"auto-baseline from step {step.step_key}",
        )
        logger.info(
            "[EvolutionEngine] seeded baseline for agent={} version={}",
            agent.id,
            baseline.new_version_id,
        )
        return

    snapshot = await record_quality_step_passed(
        db,
        agent=agent,
        verdict=verdict,
        trigger_ref_id=step.id,
        summary=(
            f"step {step.step_key} passed with score {verdict.score}"
            + (" (judge used)" if judge_payload and judge_payload.get("judge_used") else "")
        ),
    )
    if snapshot is None:
        logger.info(
            "[EvolutionEngine] agent={} skipped — no baseline",
            agent.id,
        )

    try:
        from app.services.ao.evolution_signal_service import (
            record_quality_signal,
        )
        from app.services.ao.patch_engine import generate_signal_summary

        summary_text = await generate_signal_summary(
            step_id=step.id,
            judge_payload=judge_payload,
            verdict_score=verdict.score,
        )
        await record_quality_signal(
            db,
            tenant_id=step.tenant_id,
            agent_id=agent.id,
            quality_score=verdict.score,
            rule_score=verdict.score,
            judge_payload=judge_payload,
            trigger_ref_id=step.id,
            summary=summary_text,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning(
            "[EvolutionSignal] P4 signal record failed for step={}: {}",
            step.id,
            exc,
        )


async def run_quality_check_with_verdict(
    db: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    step_id: uuid.UUID,
    verdict: QualityVerdict,
) -> dict:
    """Compatibility shim — P2.3 callers that already have a verdict payload."""
    safe_step_id = step_id
    filename = f"step_{safe_step_id}.md"
    body = (
        f"# 质控反馈\n\n"
        f"- 步骤 ID：{safe_step_id}\n"
        f"- 评分：{verdict.score}\n\n"
        f"## 反馈\n\n{verdict.feedback}\n"
    )
    try:
        result = await write_step_asset(
            db,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            step_id=step_id,
            category="quality",
            subdir="feedback",
            filename=filename,
            content=body,
            metadata={"score": int(verdict.score)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[QualityEngine] failed to persist feedback for step {}: {}",
            safe_step_id,
            exc,
        )
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "step_id": str(safe_step_id), "asset": result}


__all__ = [
    "FAIL_STATUS",
    "RETRY_STATUS",
    "TERMINAL_PASS_STATUS",
    "QualityOutcome",
    "QualityVerdict",
    "run_quality_check",
    "run_quality_check_with_verdict",
]