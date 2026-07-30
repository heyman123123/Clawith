"""Runtime contracts stay scoped to the group lifecycle tools."""

from app.services.agent_runtime.group_runtime_tools import GROUP_TOOL_NAMES
from app.services.builtin_tool_definitions import GROUP_RUNTIME_TOOL_DEFINITIONS


def test_group_runtime_exposes_structured_workflow_tools() -> None:
    names = {definition["function"]["name"] for definition in GROUP_RUNTIME_TOOL_DEFINITIONS}

    assert {
        "group_workflow_read",
        "group_workflow_start_item",
        "group_workflow_submit_evidence",
        "group_workflow_block_item",
        "group_workflow_unblock_item",
        "group_workflow_request_approval",
    } <= names <= GROUP_TOOL_NAMES
