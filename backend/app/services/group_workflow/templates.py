"""Opinionated templates that give every new group a usable first lifecycle."""

from __future__ import annotations

import uuid

from app.services.group_workflow.contracts import WorkflowItemPlan, WorkflowPlan, WorkflowStagePlan


def _stage(key: str, title: str, goal: str, *, owner: uuid.UUID | None, approval: bool = False) -> WorkflowStagePlan:
    return WorkflowStagePlan(
        key=key, title=title, goal=goal, owner_participant_id=owner, requires_approval=approval,
        acceptance_criteria=["群主确认交付满足当前阶段目标"] if approval else [],
        items=[WorkflowItemPlan(item_key=f"{key}_deliverable", title=title, description=goal, assignee_participant_id=owner)],
    )


def preset_workflow(kind: str, *, goal: str, leader_participant_id: uuid.UUID | None = None) -> WorkflowPlan:
    if kind == "agile":
        stages = [
            _stage("clarify", "需求澄清", goal, owner=leader_participant_id),
            _stage("backlog", "用户故事与优先级", "形成可执行的用户故事和优先级", owner=leader_participant_id),
            _stage("plan", "排期与方案", "确认范围、依赖、节奏和责任人", owner=leader_participant_id, approval=True),
            _stage("build", "开发交付", "完成实现并提交可验证证据", owner=leader_participant_id),
            _stage("accept", "验收", "验证交付符合需求与质量门槛", owner=leader_participant_id, approval=True),
            _stage("retro", "复盘", "总结结果、改进项和下一步", owner=leader_participant_id),
        ]
        return WorkflowPlan(name="敏捷需求", source="agile", stages=stages)
    if kind == "product_research":
        stages = [
            _stage("initiate", "立项", goal, owner=leader_participant_id, approval=True),
            _stage("solution", "方案", "形成产品与技术方案", owner=leader_participant_id),
            _stage("review", "评审", "完成跨角色评审和决策", owner=leader_participant_id, approval=True),
            _stage("implement", "实现", "按已确认方案完成实现", owner=leader_participant_id),
            _stage("verify", "测试验收", "完成质量验证和验收", owner=leader_participant_id, approval=True),
            _stage("release", "发布复盘", "发布交付并沉淀复盘", owner=leader_participant_id, approval=True),
        ]
        return WorkflowPlan(name="产研协作", source="product_research", stages=stages)
    stages = [
        _stage("clarify", "澄清目标", goal, owner=leader_participant_id),
        _stage("decompose", "拆解工作", "明确工作项、负责人和验收标准", owner=leader_participant_id, approval=True),
        _stage("deliver", "执行交付", "完成成员交付并公开提交证据", owner=leader_participant_id),
        _stage("accept", "验收汇总", "核验成果并向用户汇总", owner=leader_participant_id, approval=True),
        _stage("retro", "复盘", "记录改进项和后续行动", owner=leader_participant_id),
    ]
    return WorkflowPlan(name="协作推进", source="default", stages=stages)
