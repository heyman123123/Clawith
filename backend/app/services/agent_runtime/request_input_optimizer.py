"""Compress model-facing request inputs without changing checkpoint truth.

Applied at the Runtime model-step boundary so every provider call pays a lower
fixed tax (group policy, plan prompts, pending history, soul) while durable
snapshots remain complete for audit and tooling.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

JsonObject = dict[str, Any]
JsonValue = Any


# Compact group policy (~1.2k chars) replacing the ~8k full instruction.
COMPACT_GROUP_RUNTIME_INSTRUCTION = """
Current Run is in a native Clawith group. Follow these rules:
- Use only this group/session, injected Agent context, and enabled tools. Generic file tools (`list_files`, `read_file`, `write_file`, …) access the Agent's private Workspace only; Group Workspace paths require `group_*` tools.
- Prefer `group_write_workspace_file` for shared deliverables (`deliverables/…`). Private files are not group-shared unless mirrored after task completion.
- `@` / `mention_participant_ids` only wake Agents that must reply publicly now. Write literal `@display name` in `finish.content` and put their participant IDs in `mention_participant_ids`. Never invent IDs from names — call `group_query_members` first.
- If `group_context.group.owner_agent_id` is set, that Agent is 群主. After finishing a task/plan, `@群主` and include the 群主 participant ID so they can continue.
- `finish.content` is the only public group message: business words only — no tool names, IDs, Runtime internals, or capability narration.
- After gathering mention IDs, your next response must be exactly one `finish` call (not progress text). Multiple mentions belong in that same `mention_participant_ids` array.
- `send_message_to_agent` is private A2A only; never use it instead of group `@` when a public reply is required.
- If this Run was started by a mention, answer only your part in `current_responsibility`, then finish without reciprocal `@` greetings.
- Update only your own group memory. Ask humans for clarification in `finish.content` (do not enter `waiting_user`).
""".strip()


def _normalize_workspace_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def _bounded(text: str, max_chars: int, *, note: str) -> str:
    value = text.strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    keep = max(0, max_chars - len(note) - 1)
    return f"{value[:keep].rstrip()}\n{note}"


def _as_mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    return None


def compress_pending_session_messages(
    messages: list[Any],
    *,
    max_items: int = 10,
    max_chars_per_message: int = 500,
) -> list[JsonObject]:
    """Keep the newest pending messages with truncated content."""
    if max_items <= 0:
        return []
    selected = list(messages[-max_items:])
    output: list[JsonObject] = []
    omitted = max(0, len(messages) - len(selected))
    if omitted:
        output.append(
            {
                "role": "system",
                "content": f"[omitted {omitted} earlier pending session messages]",
            }
        )
    for raw in selected:
        if not isinstance(raw, Mapping):
            continue
        item = {str(key): deepcopy(value) for key, value in raw.items()}
        content = item.get("content")
        if isinstance(content, str) and len(content) > max_chars_per_message:
            item["content"] = _bounded(
                content,
                max_chars_per_message,
                note="[truncated]",
            )
        # Drop bulky optional fields that rarely help the next step.
        for heavy in ("tool_calls", "reasoning_content", "attachments"):
            item.pop(heavy, None)
        output.append(item)
    return output


def compress_planning_hint(
    hint: Mapping[str, Any],
    *,
    plan_prompt_max_chars: int = 2000,
) -> JsonObject:
    """Keep current responsibility; heavily bound full plan_prompt."""
    compressed: JsonObject = {}
    mode = hint.get("mode")
    if isinstance(mode, str) and mode.strip():
        compressed["mode"] = mode.strip()
    responsibility = hint.get("current_responsibility")
    if isinstance(responsibility, str) and responsibility.strip():
        compressed["current_responsibility"] = responsibility.strip()
    plan_prompt = hint.get("plan_prompt")
    if isinstance(plan_prompt, str) and plan_prompt.strip():
        # Prefer responsibility-only when plan duplicates it.
        if (
            isinstance(responsibility, str)
            and responsibility.strip()
            and plan_prompt.strip() == responsibility.strip()
        ):
            pass
        else:
            compressed["plan_prompt"] = _bounded(
                plan_prompt,
                plan_prompt_max_chars,
                note="[plan_prompt truncated; use group tools/history for full plan]",
            )
    return compressed


def compress_group_context_for_model(
    context: Mapping[str, Any],
    *,
    announcement_max_chars: int = 8000,
    memory_max_chars: int = 8000,
    workspace_max_entries: int = 40,
    plan_prompt_max_chars: int = 2000,
) -> JsonObject:
    """Shrink frozen group_context before it is serialized into the user turn."""
    compressed = deepcopy(dict(context))
    trigger = _as_mapping(compressed.get("trigger"))
    if trigger is not None:
        trigger.pop("content", None)
        compressed["trigger"] = trigger

    announcement = compressed.get("announcement")
    if isinstance(announcement, str):
        compressed["announcement"] = _bounded(
            announcement,
            announcement_max_chars,
            note="[announcement truncated]",
        )

    memory = compressed.get("agent_group_memory")
    if isinstance(memory, str):
        compressed["agent_group_memory"] = _bounded(
            memory,
            memory_max_chars,
            note="[group memory truncated]",
        )

    workspace_index = compressed.get("workspace_index")
    if isinstance(workspace_index, list):
        slim: list[JsonObject] = []
        for entry in workspace_index[:workspace_max_entries]:
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            item: JsonObject = {"path": _normalize_workspace_path(path)}
            entry_type = entry.get("type") or entry.get("kind")
            if isinstance(entry_type, str) and entry_type.strip():
                item["type"] = entry_type.strip()
            slim.append(item)
        compressed["workspace_index"] = slim
        if len(workspace_index) > workspace_max_entries:
            compressed["workspace_index_may_be_truncated"] = True

    planning_hint = _as_mapping(compressed.get("planning_hint"))
    if planning_hint is not None:
        compressed["planning_hint"] = compress_planning_hint(
            planning_hint,
            plan_prompt_max_chars=plan_prompt_max_chars,
        )
    return compressed


def compress_runtime_sections(
    sections: Mapping[str, Any],
    *,
    pending_max_items: int = 10,
    pending_max_chars: int = 500,
    announcement_max_chars: int = 8000,
    memory_max_chars: int = 8000,
    workspace_max_entries: int = 40,
    plan_prompt_max_chars: int = 2000,
) -> JsonObject:
    """Return an optimized copy of `_runtime_sections` output."""
    optimized: JsonObject = {}
    for key, value in sections.items():
        if key == "pending_session_messages_snapshot" and isinstance(value, list):
            optimized[key] = compress_pending_session_messages(
                value,
                max_items=pending_max_items,
                max_chars_per_message=pending_max_chars,
            )
            continue
        if key == "source_context" and isinstance(value, Mapping):
            source = deepcopy(dict(value))
            group_context = _as_mapping(source.get("group_context"))
            if group_context is not None:
                source["group_context"] = compress_group_context_for_model(
                    group_context,
                    announcement_max_chars=announcement_max_chars,
                    memory_max_chars=memory_max_chars,
                    workspace_max_entries=workspace_max_entries,
                    plan_prompt_max_chars=plan_prompt_max_chars,
                )
            optimized[key] = source
            continue
        optimized[key] = deepcopy(value)
    return optimized


def estimate_json_chars(value: JsonValue) -> int:
    """Rough size proxy without importing json in hot loops repeatedly."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (int, float, bool)):
        return 8
    if isinstance(value, list):
        return sum(estimate_json_chars(item) for item in value) + len(value)
    if isinstance(value, Mapping):
        total = 0
        for key, item in value.items():
            total += len(str(key)) + estimate_json_chars(item)
        return total
    return len(str(value))


__all__ = [
    "COMPACT_GROUP_RUNTIME_INSTRUCTION",
    "compress_group_context_for_model",
    "compress_pending_session_messages",
    "compress_planning_hint",
    "compress_runtime_sections",
    "estimate_json_chars",
]
