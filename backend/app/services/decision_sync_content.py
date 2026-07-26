"""Format structured decision sync payloads."""

from __future__ import annotations

import uuid
from typing import Any


def build_decision_sync_content(*, record_id: uuid.UUID, summary: dict[str, Any]) -> str:
    """Format the structured decision sync message per spec §4.7."""
    lines = [f"<!--decision_sync:{record_id}-->"]
    summary_text = summary.get("summary") or ""
    lines.append(f"📋 决策摘要：{summary_text}")

    actions = summary.get("actions") or []
    if actions:
        lines.append("🎯 下一步行动：")
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                continue
            lines.append(
                f"  {index}. {action.get('action', '')} → {action.get('owner_role', '')} → {action.get('acceptance', '')}"
            )

    risks = summary.get("risks") or []
    if risks:
        lines.append(f"⚠️ 风险与边界：{'; '.join(str(risk) for risk in risks)}")

    lines.append("🔗 决策来源：决策群 session（见 record metadata）")
    return "\n".join(lines)
