"""Wake copy for the group decision-maker agent."""

from __future__ import annotations

from typing import Any


def build_decision_wake_content(payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "state_changed")
    stage = str(payload.get("stage_title") or "当前阶段")
    stage_id = str(payload.get("stage_id") or "").strip()
    stage_arg = f"，stage_id={stage_id}" if stage_id else ""
    if kind == "approval_required":
        return (
            f"决策指令（阶段待确认）：阶段「{stage}」证据已齐，需要你拍板后才能进入下一阶段。"
            f"立刻调用 group_decision_classify_and_act（category/title/summary{stage_arg}）："
            "routine 直接确认；human_comms / external_deploy / finance / uncertain 必须私聊人类管理员求批。"
            "不要把项目拍板推给人类或成员；拍板后系统会发决策汇报。禁止干等心跳。"
        )
    return (
        f"决策指令（{kind}）：「{stage}」。"
        f"请评估是否需项目级决策；需要则调用 group_decision_classify_and_act{stage_arg}。"
    )
