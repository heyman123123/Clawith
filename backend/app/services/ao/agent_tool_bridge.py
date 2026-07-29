"""Bridge §4.1 AO / quality / delivery tools into Agent Runtime ``execute_tool``.

Hard role boundaries (需求 §1.4.2 / §8.2):

* scheduler tools — only ``workflow.scheduler`` agents
* quality tools — only ``workflow.quality`` (and future compliance/security)
* delivery tools — only ``workflow.delivery``

Cross-power calls return a typed error dict and perform no side effects.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.project import ProjectWorkflow
from app.services.ao import quality_engine, scheduler_tools
from app.services.ao.asset_writer import write_step_asset
from app.services.ao.quality_rules import RULE_CATALOG
from app.services.delivery_scoring import (
    DEFAULT_PASS_THRESHOLD,
    compute_final_score,
)
from app.services.workflow_role_seeder import SYSTEM_ROLES

PowerRole = Literal["scheduler", "quality", "delivery"]

SCHEDULER_TOOL_NAMES: frozenset[str] = frozenset(SYSTEM_ROLES["scheduler"].default_tools)
QUALITY_TOOL_NAMES: frozenset[str] = frozenset(SYSTEM_ROLES["quality"].default_tools)
DELIVERY_TOOL_NAMES: frozenset[str] = frozenset(SYSTEM_ROLES["delivery"].default_tools)
AO_TOOL_NAMES: frozenset[str] = (
    SCHEDULER_TOOL_NAMES | QUALITY_TOOL_NAMES | DELIVERY_TOOL_NAMES
) - {"send_channel_message"}
# ``send_channel_message`` stays on the general builtin path.

_TOOL_ALLOWED_ROLE: dict[str, PowerRole] = {
    **{name: "scheduler" for name in SCHEDULER_TOOL_NAMES},
    **{name: "quality" for name in QUALITY_TOOL_NAMES},
    **{name: "delivery" for name in DELIVERY_TOOL_NAMES},
}

# Tools already implemented as general builtins — still gated by role here when
# invoked through the AO bridge path; execute_tool routes AO names here first.
_PASSTHROUGH_TO_EXISTING = frozenset({"send_channel_message"})


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "error": message, **extra}


def _ok(payload: Any = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, **extra}
    if payload is not None:
        out["result"] = payload
    return out


async def resolve_agent_power_role(
    db: AsyncSession,
    agent_id: uuid.UUID,
) -> PowerRole | None:
    """Return the four-power slot for ``agent_id``, or None if not a power role."""
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None)))
    if agent is None:
        return None
    desc = (agent.role_description or "").strip()
    for role in ("scheduler", "quality", "delivery"):
        if desc == f"workflow.{role}" or desc.endswith(f".{role}"):
            return role  # type: ignore[return-value]

    wf = await db.scalar(
        select(ProjectWorkflow).where(
            or_(
                ProjectWorkflow.scheduler_agent_id == agent_id,
                ProjectWorkflow.quality_agent_id == agent_id,
                ProjectWorkflow.delivery_agent_id == agent_id,
            )
        ).limit(1)
    )
    if wf is None:
        return None
    if wf.scheduler_agent_id == agent_id:
        return "scheduler"
    if wf.quality_agent_id == agent_id:
        return "quality"
    if wf.delivery_agent_id == agent_id:
        return "delivery"
    return None


def assert_tool_allowed_for_role(tool_name: str, role: PowerRole | None) -> dict[str, Any] | None:
    """Return an error dict when ``role`` may not call ``tool_name``; else None."""
    allowed = _TOOL_ALLOWED_ROLE.get(tool_name)
    if allowed is None:
        return _err("unknown_ao_tool", f"Unknown AO tool: {tool_name}")
    if role is None:
        return _err(
            "not_a_power_role",
            f"Agent is not a workflow power role; cannot call {tool_name}",
            required_role=allowed,
        )
    if role != allowed:
        return _err(
            "role_boundary_violation",
            f"Tool {tool_name} requires role={allowed}, agent has role={role}",
            required_role=allowed,
            actual_role=role,
        )
    return None


def _parse_uuid(value: Any, field: str) -> uuid.UUID | dict[str, Any]:
    if value is None or value == "":
        return _err("invalid_tool_arguments", f"{field} is required")
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return _err("invalid_tool_arguments", f"{field} must be a UUID")


async def invoke_ao_tool(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    agent_id: uuid.UUID,
    db: AsyncSession,
) -> dict[str, Any]:
    """Dispatch one §4.1 tool call after enforcing the hard role boundary."""
    args = dict(arguments or {})
    role = await resolve_agent_power_role(db, agent_id)
    denied = assert_tool_allowed_for_role(name, role)
    if denied is not None:
        logger.info(
            "[AOToolBridge] denied tool={} agent={} role={} code={}",
            name,
            agent_id,
            role,
            denied.get("error_code"),
        )
        return denied

    if name in _PASSTHROUGH_TO_EXISTING:
        return _ok({"delegated": True, "tool": name, "note": "use builtin send_channel_message handler"})

    try:
        if name == "ao_parse_workflow":
            return _ok(scheduler_tools.ao_parse_workflow(str(args.get("workflow_id", ""))))
        if name == "ao_get_execution_plan":
            return _ok(scheduler_tools.ao_get_execution_plan(str(args.get("workflow_id", ""))))
        if name == "ao_resume_from_step":
            return _ok(
                scheduler_tools.ao_resume_from_step(
                    str(args.get("workflow_id", "")),
                    str(args.get("from_step", "")),
                    args.get("feedback"),
                )
            )
        if name == "init_workflow_dir":
            return _ok(scheduler_tools.init_workflow_dir(str(args.get("workflow_id", ""))))
        if name == "update_workflow_status":
            return _ok(
                scheduler_tools.update_workflow_status(
                    str(args.get("workflow_id", "")),
                    str(args.get("status", "")),
                    note=args.get("note"),
                )
            )
        if name == "update_project_status":
            return _ok(
                scheduler_tools.update_project_status(
                    str(args.get("workflow_id", "")),
                    str(args.get("status", "")),
                )
            )
        if name == "audit_skill_application":
            return _ok(
                scheduler_tools.audit_skill_application(
                    str(args.get("workflow_id", "")),
                    str(args.get("skill_id", "")),
                    str(args.get("level", "low")),
                )
            )
        if name == "dispatch_task_to_role":
            return await _dispatch_task(db, args)
        if name == "trigger_approval_node":
            return await _trigger_approval(db, args)

        if name == "quality_check_step":
            return await _quality_check_step(db, args)
        if name == "quality_check_full":
            return await _quality_check_full(db, args)
        if name == "get_quality_rules":
            return _ok(
                [
                    {"key": r.key, "description": r.description}
                    for r in RULE_CATALOG
                ]
            )
        if name == "verify_rectification":
            return _ok(
                {
                    "verified": bool(args.get("accepted", False)),
                    "notes": args.get("notes") or "",
                }
            )
        if name == "generate_quality_report":
            return await _write_role_asset(db, args, category="quality", filename="quality_report.md")
        if name == "write_quality_asset":
            return await _write_role_asset(db, args, category="quality")
        if name == "submit_feedback_to_role":
            return _ok(
                {
                    "submitted": True,
                    "target_role": args.get("target_role"),
                    "feedback": (args.get("feedback") or "")[:2000],
                }
            )
        if name == "learn_quality_skill":
            return _ok({"queued": True, "skill_hint": args.get("skill_hint") or args.get("topic")})

        if name == "compile_delivery_package":
            return await _write_role_asset(
                db, args, category="delivery", filename="delivery_package.md", default_body="# 交付包\n"
            )
        if name == "check_requirement_coverage":
            coverage = float(args.get("coverage_score", args.get("coverage", 0)) or 0)
            quality = float(args.get("quality_score", args.get("quality", 0)) or 0)
            verdict = compute_final_score(quality=quality, coverage=coverage)
            return _ok(
                {
                    "final_score": verdict.final_score,
                    "passed": verdict.passed,
                    "pass_threshold": verdict.pass_threshold or DEFAULT_PASS_THRESHOLD,
                    "quality": verdict.quality,
                    "coverage": verdict.coverage,
                }
            )
        if name == "submit_approval_request":
            return _ok(
                {
                    "submitted": True,
                    "workflow_id": args.get("workflow_id"),
                    "message": "Delivery approval request recorded for human delivery manager.",
                }
            )
        if name == "parse_rectification_comments":
            text = str(args.get("comments") or "")
            items = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
            return _ok({"items": items[:50]})
        if name == "generate_delivery_report":
            return await _write_role_asset(
                db, args, category="delivery", filename="delivery_report.md", default_body="# 交付报告\n"
            )
        if name == "write_delivery_asset":
            return await _write_role_asset(db, args, category="delivery")
        if name == "update_approval_status":
            return _ok({"status": args.get("status") or "pending", "workflow_id": args.get("workflow_id")})
        if name == "learn_delivery_skill":
            return _ok({"queued": True, "skill_hint": args.get("skill_hint") or args.get("topic")})

        return _err("unknown_ao_tool", f"No handler for {name}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[AOToolBridge] tool={} failed: {}", name, exc)
        return _err("ao_tool_failed", f"{type(exc).__name__}: {exc}")


async def _dispatch_task(db: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _parse_uuid(args.get("workflow_id"), "workflow_id")
    if isinstance(workflow_id, dict):
        return workflow_id
    target = args.get("role_agent_id") or args.get("target_agent_id") or args.get("agent_id")
    target_id = _parse_uuid(target, "role_agent_id")
    if isinstance(target_id, dict):
        return target_id
    summary = str(args.get("task_summary") or args.get("task") or "").strip()
    if not summary:
        return _err("invalid_tool_arguments", "task_summary is required")
    step_id = args.get("step_id")
    step_ref = str(step_id) if step_id else None

    from app.services.ao.scheduler_tools import dispatch_task_to_role, scheduler_tool_context

    with scheduler_tool_context(db=db, workflow_id=workflow_id):
        result = await dispatch_task_to_role(
            role_agent_id=str(target_id),
            task_summary=summary,
            inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else None,
            expected_outputs=args.get("expected_outputs")
            if isinstance(args.get("expected_outputs"), list)
            else None,
            step_id=step_ref,
        )
    return _ok(result)


async def _trigger_approval(db: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _parse_uuid(args.get("workflow_id"), "workflow_id")
    if isinstance(workflow_id, dict):
        return workflow_id
    step_id = str(args.get("step_id") or "").strip()
    if not step_id:
        return _err("invalid_tool_arguments", "step_id is required")
    prompt = str(args.get("prompt") or args.get("reason") or "请审批此步骤")
    approvers = args.get("approver_user_ids")
    if not isinstance(approvers, list) or not approvers:
        return _err("invalid_tool_arguments", "approver_user_ids is required")

    from app.services.ao.scheduler_tools import scheduler_tool_context, trigger_approval_node

    with scheduler_tool_context(db=db, workflow_id=workflow_id):
        result = await trigger_approval_node(
            workflow_id=str(workflow_id),
            step_id=step_id,
            prompt=prompt,
            approver_user_ids=[str(x) for x in approvers],
        )
    return _ok(result)


async def _quality_check_step(db: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _parse_uuid(args.get("workflow_id"), "workflow_id")
    if isinstance(workflow_id, dict):
        return workflow_id
    step_id = _parse_uuid(args.get("step_id"), "step_id")
    if isinstance(step_id, dict):
        return step_id
    tenant_id = _parse_uuid(args.get("tenant_id"), "tenant_id")
    if isinstance(tenant_id, dict):
        # Fall back to workflow.tenant_id
        wf = await db.scalar(select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id))
        if wf is None:
            return tenant_id
        tenant_id = wf.tenant_id

    outcome = await quality_engine.run_quality_check(
        db,
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        step_id=step_id,
        output_text=args.get("output_text"),
        enable_llm_judge=bool(args.get("enable_llm_judge", True)),
    )
    return _ok(
        {
            "score": outcome.verdict.score,
            "passed": outcome.verdict.passed,
            "next_status": outcome.next_status,
            "retry_count": outcome.retry_count,
            "feedback": outcome.verdict.feedback,
        }
    )


async def _quality_check_full(db: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _parse_uuid(args.get("workflow_id"), "workflow_id")
    if isinstance(workflow_id, dict):
        return workflow_id
    tenant_id = _parse_uuid(args.get("tenant_id"), "tenant_id")
    if isinstance(tenant_id, dict):
        wf = await db.scalar(select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id))
        if wf is None:
            return tenant_id
        tenant_id = wf.tenant_id
    outcome = await quality_engine.run_quality_check_full(
        db,
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        enable_llm_judge=bool(args.get("enable_llm_judge", False)),
    )
    return _ok(
        {
            "passed": outcome.passed,
            "average_score": outcome.average_score,
            "checked_count": outcome.checked_count,
            "failed_step_ids": [str(x) for x in outcome.failed_step_ids],
        }
    )


async def _write_role_asset(
    db: AsyncSession,
    args: dict[str, Any],
    *,
    category: str,
    filename: str | None = None,
    default_body: str = "",
) -> dict[str, Any]:
    workflow_id = _parse_uuid(args.get("workflow_id"), "workflow_id")
    if isinstance(workflow_id, dict):
        return workflow_id
    wf = await db.scalar(select(ProjectWorkflow).where(ProjectWorkflow.id == workflow_id))
    if wf is None:
        return _err("workflow_not_found", f"workflow {workflow_id} not found")
    step_raw = args.get("step_id")
    step_id = None
    if step_raw:
        parsed = _parse_uuid(step_raw, "step_id")
        if isinstance(parsed, dict):
            return parsed
        step_id = parsed
    body = str(args.get("content") or args.get("body") or default_body or "# asset\n")
    fname = str(args.get("filename") or filename or "asset.md")
    result = await write_step_asset(
        db,
        workflow_id=workflow_id,
        tenant_id=wf.tenant_id,
        step_id=step_id,
        category=category,  # type: ignore[arg-type]
        subdir=str(args.get("subdir") or "assets"),
        filename=fname,
        content=body,
        metadata={"source": "agent_tool_bridge"},
    )
    return _ok(result)


def format_ao_tool_result(payload: dict[str, Any]) -> str:
    """Serialize bridge result for the legacy string-returning ``execute_tool`` API."""
    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = [
    "AO_TOOL_NAMES",
    "DELIVERY_TOOL_NAMES",
    "QUALITY_TOOL_NAMES",
    "SCHEDULER_TOOL_NAMES",
    "assert_tool_allowed_for_role",
    "format_ao_tool_result",
    "invoke_ao_tool",
    "resolve_agent_power_role",
]
