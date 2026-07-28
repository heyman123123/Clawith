"""Idempotent workflow role provisioning — scheduler / quality / delivery agents.

Each role ships as a builtin ``AgentTemplate`` so HR-built project groups can
clone a deterministic scheduler + quality + delivery trio per tenant without
running DAO/role-pool lookups. See 需求 §1.4 and §4.1 for the surrounding
"four-powers + multi-role" contract:

- Scheduler (调度): DAG、dispatch、推进、escalation；不执行、不定质量、不直接交付
- Quality (质控): 质检、scoring、整改；不改业务稿、不调度
- Delivery (交付): 整理交付、对接真人交付经理；不定质量、不改业务稿
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.models.agent import Agent, AgentPermission, AgentTemplate
from app.models.participant import Participant
from app.services.agent_manager import agent_manager
from app.services.storage import store_agent_bytes

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()


@dataclass(frozen=True)
class SystemRoleSpec:
    """Static description of one workflow role."""

    role_key: str
    display_name: str
    description: str
    soul_body: str
    default_tools: tuple[str, ...]


_SCHEDULER_TOOLS: tuple[str, ...] = (
    "ao_parse_workflow",
    "ao_get_execution_plan",
    "ao_resume_from_step",
    "dispatch_task_to_role",
    "init_workflow_dir",
    "update_workflow_status",
    "send_channel_message",
    "update_project_status",
    "trigger_approval_node",
    "audit_skill_application",
)

_QUALITY_TOOLS: tuple[str, ...] = (
    "quality_check_step",
    "quality_check_full",
    "verify_rectification",
    "get_quality_rules",
    "generate_quality_report",
    "write_quality_asset",
    "submit_feedback_to_role",
    "learn_quality_skill",
)

_DELIVERY_TOOLS: tuple[str, ...] = (
    "compile_delivery_package",
    "check_requirement_coverage",
    "submit_approval_request",
    "parse_rectification_comments",
    "generate_delivery_report",
    "write_delivery_asset",
    "update_approval_status",
    "learn_delivery_skill",
)


SCHEDULER_SOUL = """\
# 项目调度官 (Project Scheduler)

## 身份
你是 Clawith 工作流执行群的"调度官"，担任群主，唯一职责是围绕 **DAG 编排** 推动整条执行链路前进；
你既不亲自实现业务交付物，也不替质控和交付做判断，只在群内做调度、推进与异常协调。

## 核心职责
1. 解析 AO YAML 工作流，建立 DAG（依赖、并行、重试、断点续跑）。
2. 把 DAG 的步骤按依赖顺序 **dispatch** 给对应的执行角色（执行位 N 个），并确保角色到位、上下文齐全。
3. 维护执行视图、推进度、记录审批/决策挂起点，触发 `trigger_approval_node`。
4. 协调异常：失败重试、跳过、人工复核、技能自学申请、升级到决策群或股东群。
5. 实时回写 `project_workflow_runs.status`、`update_project_status` 并同步到群公告。

## 边界
- **不执行**：你不写业务稿、不产出方案文本、不写代码，只调度别人做事。
- **不定质量**：当质控官给出评分与整改意见时，由你传达给执行角色，但你不打分、不评价业务稿内容。
- **不直接交付**：交付包由交付协调官整理与提交，你仅负责把质控通过的产物推到交付阶段。
- 不发质量/交付结论性话术；所有越界的话术必须由对位角色发出。

## 工具集
ao_parse_workflow / ao_get_execution_plan / ao_resume_from_step /
dispatch_task_to_role / init_workflow_dir / update_workflow_status /
send_channel_message / update_project_status / trigger_approval_node /
audit_skill_application。
"""

QUALITY_SOUL = """\
# 质量评审官 (Quality Reviewer)

## 身份
你是 Clawith 工作流执行群的"质控官"，独立于执行位，专职对每一步骤及最终交付物做 **质检、评分、整改追踪**；
你既不亲自实现业务，也不改原始业务稿，更不调度任何人——只负责质量底线与回退闭环。

## 核心职责
1. 按规则引擎与历史评分模型，对单个步骤产出物做 `quality_check_step`、全流程做 `quality_check_full`。
2. 给出百分制评分、问题清单、整改建议并通过 `submit_feedback_to_role` 回退到对应执行角色。
3. 维护 `workflow_quality_records` 与 `workflow_quality_rules`，沉淀规则与历史评分。
4. 跟踪 `verify_rectification` 闭环；通过即推进，不过即退回到执行位继续重试（≤重试上限）。
5. 生成 `generate_quality_report` 沉淀到 `03-质量管控/` 群文件夹，并可在群内公示过程结论。

## 边界
- **不改原始业务稿**：你只能给反馈与整改要求；不允许直接编辑 `/workspace/`、`01-步骤输出/`、`06-最终交付/` 下的执行产物。
- **不调度**：你不接 DAG 也不分发任务，所有推进诉求必须经过项目调度官。
- **不直接交付**：验收与对外提交由交付协调官执行；你仅出具结论。
- 不因用户或上游压力违规放行；评分需基于可重复证据（规则命中、模型打分、人工复核）。

## 工具集
quality_check_step / quality_check_full / verify_rectification /
get_quality_rules / generate_quality_report / write_quality_asset /
submit_feedback_to_role / learn_quality_skill。
"""

DELIVERY_SOUL = """\
# 交付协调官 (Delivery Coordinator)

## 身份
你是 Clawith 工作流执行群的"交付官"，专职在质控闭环之后 **整理交付包、对接真人交付经理**、提交验收申请；
你不直接为业务质量负责，也不修改执行位产出的业务稿，只负责最后阶段的交付体验与闭环。

## 核心职责
1. 接收质控通过的产物，按 `compile_delivery_package` 标准汇总清单、索引、范围、版本与变更说明。
2. 执行 `check_requirement_coverage` 比对原始需求覆盖度，识别未达成项并申请回退。
3. 解析反馈：`parse_rectification_comments` 把真人交付经理或客户意见转结构化整改任务。
4. 通过 `submit_approval_request` / `update_approval_status` 完成验收流转，闭环 ≤3 次整改预警。
5. 落盘 `04-交付验收/`、`06-最终交付/` 群文件夹；支持续跑与正式包发布。

## 边界
- **不定质量**：分数与质量结论由质量评审官出具；你不复评业务稿。
- **不改业务稿**：只整理、引用、汇总，不编辑 `01-步骤输出/` `06-最终交付/` 下的原始产出。
- **不调度**：所有推进依赖调度官；交付冲突须升级到调度官/决策群/股东群。
- 验收 ≥90 分准入由质控 + 真人交付经理双维度把关；未达标直接驳回，不强行放行。

## 工具集
compile_delivery_package / check_requirement_coverage /
submit_approval_request / parse_rectification_comments /
generate_delivery_report / write_delivery_asset / update_approval_status /
learn_delivery_skill。
"""


SYSTEM_ROLES: dict[str, SystemRoleSpec] = {
    "scheduler": SystemRoleSpec(
        role_key="scheduler",
        display_name="项目调度官",
        description="调度官：解析 DAG、dispatch、推进、异常协调；不执行、不定质量、不直接交付。",
        soul_body=SCHEDULER_SOUL,
        default_tools=_SCHEDULER_TOOLS,
    ),
    "quality": SystemRoleSpec(
        role_key="quality",
        display_name="质量评审官",
        description="质控官：质检、评分、整改闭环；不改原始业务稿、不调度。",
        soul_body=QUALITY_SOUL,
        default_tools=_QUALITY_TOOLS,
    ),
    "delivery": SystemRoleSpec(
        role_key="delivery",
        display_name="交付协调官",
        description="交付官：整理交付包、对接真人交付经理；不定质量、不改业务稿。",
        soul_body=DELIVERY_SOUL,
        default_tools=_DELIVERY_TOOLS,
    ),
}


def _workflow_template_key(role_key: str) -> str:
    return f"workflow_{role_key}"


def _workflow_template_dir_name(role_key: str) -> str:
    return f"workflow_{role_key}"


def _soul_md_filename() -> str:
    return "soul.md"


def _render_soul(spec: SystemRoleSpec) -> str:
    """Compose the canonical soul markdown body for an Agent clone."""
    tools = ", ".join(spec.default_tools)
    header = f"# {spec.display_name}（{spec.role_key}）\n\n"
    meta = f"## Role Metadata\n- role_key: {spec.role_key}\n- scope: system\n- default_tools: {tools}\n\n"
    return f"{header}{meta}{spec.soul_body}"


def _template_dir_for(role_key: str) -> Path:
    """Return the (writable) template directory for a given role_key.

    Stored under the configured ``AGENT_TEMPLATE_DIR`` so it lives next to
    other role templates and tests can swap the directory via fixtures.
    """
    return Path(settings.AGENT_TEMPLATE_DIR) / _workflow_template_dir_name(role_key)


async def _write_template_soul_file(spec: SystemRoleSpec) -> Path:
    """Materialize ``<AGENT_TEMPLATE_DIR>/workflow_<role_key>/soul.md``.

    Writes are best-effort within the configured template root — they never
    block test runs and they survive subsequent ``ensure_workflow_system_roles``
    calls by being upsert (overwritten by the latest canonical soul body).
    """
    target_dir = _template_dir_for(spec.role_key)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / _soul_md_filename()
    target_path.write_text(_render_soul(spec), encoding="utf-8")
    return target_path


async def _get_or_create_template(
    db: AsyncSession,
    spec: SystemRoleSpec,
    *,
    creator_id: uuid.UUID,
) -> AgentTemplate:
    """Idempotent: builtin template keyed by ``workflow_<role_key>`` name."""
    template_name = _workflow_template_key(spec.role_key)
    result = await db.execute(
        select(AgentTemplate).where(
            AgentTemplate.name == template_name,
            AgentTemplate.is_builtin.is_(True),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        template = AgentTemplate(
            name=template_name,
            description=spec.description,
            icon="🛰" if spec.role_key == "scheduler" else "🛡" if spec.role_key == "quality" else "📦",
            category="workflow",
            is_builtin=True,
            soul_template=spec.soul_body,
            default_skills=list(spec.default_tools),
            default_mcp_servers=[],
            default_autonomy_policy={},
            capability_bullets=[
                f"{spec.display_name}：{spec.description}",
                f"内置工具：{', '.join(spec.default_tools[:4])} ...",
                "由 Clawith 工作流引擎复用，禁止越权改派职责。",
            ],
            created_by=creator_id,
        )
        db.add(template)
        await db.flush()
        logger.info(
            "[WorkflowRoleSeeder] Created builtin template {} for role_key={}",
            template_name,
            spec.role_key,
        )
    else:
        template.description = spec.description
        template.soul_template = spec.soul_body
        template.default_skills = list(spec.default_tools)
    await db.flush()
    return template


async def _get_or_create_agent(
    db: AsyncSession,
    spec: SystemRoleSpec,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
    template: AgentTemplate,
) -> Agent:
    """Idempotent: per-tenant clone named after the role display name."""
    result = await db.execute(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.name == spec.display_name,
            Agent.is_system.is_(True),
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        agent = Agent(
            id=uuid.uuid4(),
            name=spec.display_name,
            role_description=f"workflow.{spec.role_key}",
            bio=spec.description,
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
            is_system=True,
            reusable=False,
            primary_model_id=model_id,
            access_mode="company",
            company_access_level="use",
            heartbeat_enabled=False,
            template_id=template.id,
        )
        db.add_all(
            (
                agent,
                AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"),
                Participant(
                    type="agent",
                    ref_id=agent.id,
                    display_name=agent.name,
                    avatar_url=agent.avatar_url,
                ),
            )
        )
        await db.flush()
        await agent_manager.initialize_agent_files(db, agent)
        logger.info(
            "[WorkflowRoleSeeder] Created workflow agent {} for role_key={} tenant={}",
            spec.display_name,
            spec.role_key,
            tenant_id,
        )
    else:
        if model_id is not None and agent.primary_model_id is None:
            agent.primary_model_id = model_id
        agent.template_id = template.id

    await store_agent_bytes(
        agent.id,
        _soul_md_filename(),
        _render_soul(spec).encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    await db.flush()
    return agent


async def _ensure_one_role(
    db: AsyncSession,
    spec: SystemRoleSpec,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
) -> Agent:
    template = await _get_or_create_template(
        db,
        spec,
        creator_id=creator_id,
    )
    agent = await _get_or_create_agent(
        db,
        spec,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
        template=template,
    )
    await _write_template_soul_file(spec)
    return agent


async def ensure_workflow_system_roles(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None = None,
) -> dict[str, Agent]:
    """Idempotently provision scheduler/quality/delivery agents for a tenant.

    Safe to call on registration, project-create, startup-backfill or any
    number of times — existing rows are reused; only the missing ones are
    created. Returns ``{"scheduler": agent, "quality": agent, "delivery": agent}``.
    """
    await db.flush()
    agents: dict[str, Agent] = {}
    for role_key, spec in SYSTEM_ROLES.items():
        agents[role_key] = await _ensure_one_role(
            db,
            spec,
            tenant_id=tenant_id,
            creator_id=creator_id,
            model_id=model_id,
        )
    await db.flush()
    logger.info(
        "[WorkflowRoleSeeder] Provisioned scheduler/quality/delivery for tenant {}",
        tenant_id,
    )
    return agents


async def try_ensure_workflow_system_roles(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
    context: str,
) -> dict[str, Agent] | None:
    """Best-effort wrapper used by callers that should not abort on seed failure."""
    try:
        return await ensure_workflow_system_roles(
            db,
            tenant_id=tenant_id,
            creator_id=creator_id,
            model_id=model_id,
        )
    except Exception as exc:  # noqa: BLE001 - defensive logging, matches governance_group_backfill
        logger.exception(
            "[WorkflowRoleSeeder] {} failed for tenant {} creator {}: {}",
            context,
            tenant_id,
            creator_id,
            exc,
        )
        return None
