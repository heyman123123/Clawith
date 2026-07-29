"""AO / quality / delivery builtin tool schemas (需求 §4.1).

Appended into ``_BUILTIN_TOOL_SOURCE`` so the seeder + model contract stay
in one place.  Execution lives in ``app.services.ao.agent_tool_bridge``.
"""

from __future__ import annotations

_WF = {"type": "string", "description": "ProjectWorkflow / AO workflow UUID"}
_STEP = {"type": "string", "description": "WorkflowRunStep id or step_key"}


def _tool(
    name: str,
    display_name: str,
    description: str,
    properties: dict,
    required: list[str] | None = None,
    *,
    icon: str = "🛰",
) -> dict:
    schema: dict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "category": "workflow",
        "icon": icon,
        "is_default": False,
        "parameters_schema": schema,
        "config": {},
        "config_schema": {},
    }


AO_BUILTIN_TOOL_DEFINITIONS: list[dict] = [
    # ── Scheduler ──
    _tool(
        "ao_parse_workflow",
        "Parse AO Workflow",
        "Validate and parse the AO YAML for a workflow; returns steps_count.",
        {"workflow_id": _WF},
        ["workflow_id"],
    ),
    _tool(
        "ao_get_execution_plan",
        "Get AO Execution Plan",
        "Return the AO execution plan as a list of step dicts.",
        {"workflow_id": _WF},
        ["workflow_id"],
    ),
    _tool(
        "ao_resume_from_step",
        "Resume AO From Step",
        "Resume an AO workflow from a checkpoint step with optional feedback.",
        {
            "workflow_id": _WF,
            "from_step": {"type": "string", "description": "Step id to resume from"},
            "feedback": {"type": "string", "description": "Optional reviewer feedback"},
        },
        ["workflow_id", "from_step"],
    ),
    _tool(
        "dispatch_task_to_role",
        "Dispatch Task To Role",
        "Hand off a DAG step task to an executor agent (调度官 only).",
        {
            "workflow_id": _WF,
            "role_agent_id": {"type": "string", "description": "Target executor Agent UUID"},
            "task_summary": {"type": "string", "description": "Task brief for the executor"},
            "step_id": _STEP,
            "inputs": {"type": "object"},
            "expected_outputs": {"type": "array", "items": {"type": "string"}},
        },
        ["workflow_id", "role_agent_id", "task_summary"],
    ),
    _tool(
        "init_workflow_dir",
        "Init Workflow Directory",
        "Create the eight-bucket asset directory scaffold for a workflow.",
        {"workflow_id": _WF},
        ["workflow_id"],
    ),
    _tool(
        "update_workflow_status",
        "Update Workflow Status",
        "Append a status audit entry for the workflow run directory.",
        {
            "workflow_id": _WF,
            "status": {"type": "string"},
            "note": {"type": "string"},
        },
        ["workflow_id", "status"],
    ),
    _tool(
        "update_project_status",
        "Update Project Status",
        "Update the Clawith project/workflow high-level status (scheduler).",
        {
            "workflow_id": _WF,
            "status": {"type": "string"},
        },
        ["workflow_id", "status"],
    ),
    _tool(
        "trigger_approval_node",
        "Trigger Approval Node",
        "Open a human approval card for a workflow step.",
        {
            "workflow_id": _WF,
            "step_id": _STEP,
            "prompt": {"type": "string"},
            "approver_user_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "User UUIDs who may approve",
            },
        },
        ["workflow_id", "step_id", "prompt", "approver_user_ids"],
    ),
    _tool(
        "audit_skill_application",
        "Audit Skill Application",
        "Record a skill-application audit event for the workflow.",
        {
            "workflow_id": _WF,
            "skill_id": {"type": "string"},
            "level": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        ["workflow_id", "skill_id"],
    ),
    # send_channel_message already exists as a general builtin — still listed in
    # scheduler default_tools; runtime uses the existing handler when not AO-gated.
    # ── Quality ──
    _tool(
        "quality_check_step",
        "Quality Check Step",
        "Run rule (+ optional LLM judge) quality check for one step.",
        {
            "workflow_id": _WF,
            "step_id": _STEP,
            "tenant_id": {"type": "string"},
            "output_text": {"type": "string"},
            "enable_llm_judge": {"type": "boolean", "default": True},
        },
        ["workflow_id", "step_id"],
        icon="🛡",
    ),
    _tool(
        "quality_check_full",
        "Quality Check Full",
        "Run quality checks across all steps of a workflow.",
        {
            "workflow_id": _WF,
            "tenant_id": {"type": "string"},
            "enable_llm_judge": {"type": "boolean", "default": False},
        },
        ["workflow_id"],
        icon="🛡",
    ),
    _tool(
        "verify_rectification",
        "Verify Rectification",
        "Confirm whether a rectification package is accepted.",
        {
            "workflow_id": _WF,
            "accepted": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        ["workflow_id", "accepted"],
        icon="🛡",
    ),
    _tool(
        "get_quality_rules",
        "Get Quality Rules",
        "List the active rule-catalog entries for quality evaluation.",
        {},
        icon="🛡",
    ),
    _tool(
        "generate_quality_report",
        "Generate Quality Report",
        "Write a quality report asset under the quality bucket.",
        {
            "workflow_id": _WF,
            "step_id": _STEP,
            "content": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["workflow_id", "content"],
        icon="🛡",
    ),
    _tool(
        "write_quality_asset",
        "Write Quality Asset",
        "Write an arbitrary quality-control asset file.",
        {
            "workflow_id": _WF,
            "step_id": _STEP,
            "content": {"type": "string"},
            "filename": {"type": "string"},
            "subdir": {"type": "string"},
        },
        ["workflow_id", "content"],
        icon="🛡",
    ),
    _tool(
        "submit_feedback_to_role",
        "Submit Feedback To Role",
        "Send structured quality feedback to an executor role.",
        {
            "workflow_id": _WF,
            "target_role": {"type": "string"},
            "feedback": {"type": "string"},
        },
        ["workflow_id", "feedback"],
        icon="🛡",
    ),
    _tool(
        "learn_quality_skill",
        "Learn Quality Skill",
        "Queue a quality-domain skill learning request.",
        {"skill_hint": {"type": "string"}, "topic": {"type": "string"}},
        icon="🛡",
    ),
    # ── Delivery ──
    _tool(
        "compile_delivery_package",
        "Compile Delivery Package",
        "Compile and write the delivery package index asset.",
        {
            "workflow_id": _WF,
            "content": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["workflow_id"],
        icon="📦",
    ),
    _tool(
        "check_requirement_coverage",
        "Check Requirement Coverage",
        "Compute two-dimension delivery score (quality 60% + coverage 40%).",
        {
            "quality_score": {"type": "number"},
            "coverage_score": {"type": "number"},
            "workflow_id": _WF,
        },
        ["quality_score", "coverage_score"],
        icon="📦",
    ),
    _tool(
        "submit_approval_request",
        "Submit Delivery Approval Request",
        "Request human delivery-manager acceptance for the package.",
        {"workflow_id": _WF, "notes": {"type": "string"}},
        ["workflow_id"],
        icon="📦",
    ),
    _tool(
        "parse_rectification_comments",
        "Parse Rectification Comments",
        "Parse free-text rectification comments into checklist items.",
        {"comments": {"type": "string"}},
        ["comments"],
        icon="📦",
    ),
    _tool(
        "generate_delivery_report",
        "Generate Delivery Report",
        "Write a delivery report asset.",
        {
            "workflow_id": _WF,
            "content": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["workflow_id"],
        icon="📦",
    ),
    _tool(
        "write_delivery_asset",
        "Write Delivery Asset",
        "Write an arbitrary delivery asset file.",
        {
            "workflow_id": _WF,
            "content": {"type": "string"},
            "filename": {"type": "string"},
            "subdir": {"type": "string"},
        },
        ["workflow_id", "content"],
        icon="📦",
    ),
    _tool(
        "update_approval_status",
        "Update Approval Status",
        "Update delivery approval status metadata.",
        {
            "workflow_id": _WF,
            "status": {"type": "string"},
        },
        ["workflow_id", "status"],
        icon="📦",
    ),
    _tool(
        "learn_delivery_skill",
        "Learn Delivery Skill",
        "Queue a delivery-domain skill learning request.",
        {"skill_hint": {"type": "string"}, "topic": {"type": "string"}},
        icon="📦",
    ),
]


__all__ = ["AO_BUILTIN_TOOL_DEFINITIONS"]
