"""Unit tests for model-facing request input compression."""

from app.services.agent_runtime.request_input_optimizer import (
    COMPACT_GROUP_RUNTIME_INSTRUCTION,
    compress_group_context_for_model,
    compress_pending_session_messages,
    compress_planning_hint,
    compress_runtime_sections,
    estimate_json_chars,
)


def test_compact_group_instruction_is_much_shorter_than_legacy() -> None:
    # Legacy policy lived inline in model_step_service (~8k chars).
    assert 800 < len(COMPACT_GROUP_RUNTIME_INSTRUCTION) < 2500
    assert "finish.content" in COMPACT_GROUP_RUNTIME_INSTRUCTION
    assert "group_query_members" in COMPACT_GROUP_RUNTIME_INSTRUCTION


def test_compress_pending_session_messages_keeps_newest_and_truncates() -> None:
    messages = [
        {"role": "user", "content": f"msg-{i}-" + ("x" * 800), "tool_calls": [{"id": "t"}]}
        for i in range(12)
    ]

    compressed = compress_pending_session_messages(
        messages,
        max_items=3,
        max_chars_per_message=40,
    )

    assert compressed[0]["content"] == "[omitted 9 earlier pending session messages]"
    assert len(compressed) == 4
    assert compressed[-1]["content"].endswith("[truncated]")
    assert "tool_calls" not in compressed[-1]
    assert "msg-11" in compressed[-1]["content"]


def test_compress_planning_hint_drops_duplicate_plan_prompt() -> None:
    hint = {
        "mode": "execute",
        "current_responsibility": "Ship the report",
        "plan_prompt": "Ship the report",
        "extra": "ignored",
    }

    compressed = compress_planning_hint(hint, plan_prompt_max_chars=100)

    assert compressed == {
        "mode": "execute",
        "current_responsibility": "Ship the report",
    }


def test_compress_planning_hint_bounds_long_plan_prompt() -> None:
    hint = {
        "current_responsibility": "Do A",
        "plan_prompt": "FULL PLAN " + ("y" * 500),
    }

    compressed = compress_planning_hint(hint, plan_prompt_max_chars=40)

    assert "current_responsibility" in compressed
    assert compressed["plan_prompt"].endswith(
        "[plan_prompt truncated; use group tools/history for full plan]"
    )
    assert len(compressed["plan_prompt"]) <= 80


def test_compress_group_context_strips_trigger_text_and_slims_workspace() -> None:
    context = {
        "trigger": {"message_id": "m1", "content": "huge duplicate body"},
        "announcement": "A" * 100,
        "agent_group_memory": "M" * 100,
        "workspace_index": [
            {"path": f"deliverables/{i}.md", "type": "file", "size": 999}
            for i in range(5)
        ],
        "planning_hint": {
            "mode": "plan",
            "current_responsibility": "Write plan",
            "plan_prompt": "P" * 200,
        },
    }

    compressed = compress_group_context_for_model(
        context,
        announcement_max_chars=20,
        memory_max_chars=20,
        workspace_max_entries=2,
        plan_prompt_max_chars=30,
    )

    assert "content" not in compressed["trigger"]
    assert compressed["trigger"]["message_id"] == "m1"
    assert compressed["announcement"].endswith("[announcement truncated]")
    assert compressed["agent_group_memory"].endswith("[group memory truncated]")
    assert len(compressed["workspace_index"]) == 2
    assert compressed["workspace_index_may_be_truncated"] is True
    assert "size" not in compressed["workspace_index"][0]
    assert compressed["planning_hint"]["plan_prompt"].endswith(
        "[plan_prompt truncated; use group tools/history for full plan]"
    )


def test_compress_runtime_sections_optimizes_known_hotspots() -> None:
    sections = {
        "session_context_snapshot": {"session_id": "s1"},
        "pending_session_messages_snapshot": [
            {"role": "user", "content": "old"},
            {"role": "user", "content": "new-" + ("z" * 200)},
        ],
        "source_context": {
            "group_context": {
                "announcement": "N" * 300,
                "workspace_index": [{"path": "a.md", "type": "file"}],
                "planning_hint": {
                    "current_responsibility": "R",
                    "plan_prompt": "PLAN " + ("q" * 400),
                },
            },
            "a2a_mode": True,
        },
    }

    optimized = compress_runtime_sections(
        sections,
        pending_max_items=1,
        pending_max_chars=20,
        announcement_max_chars=40,
        memory_max_chars=40,
        workspace_max_entries=10,
        plan_prompt_max_chars=40,
    )

    pending = optimized["pending_session_messages_snapshot"]
    assert pending[0]["content"] == "[omitted 1 earlier pending session messages]"
    assert pending[-1]["content"].endswith("[truncated]")
    group = optimized["source_context"]["group_context"]
    assert group["announcement"].endswith("[announcement truncated]")
    assert estimate_json_chars(optimized) < estimate_json_chars(sections)
