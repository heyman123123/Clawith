"""Official workflow templates catalog seed (P7 + 需求 §8.7).

Curates the 30 official AO templates referenced in
``需求.md`` §4.4 + §4.6 + §8.7.  Each row is idempotent (a second
``seed_official_workflow_templates`` call is a no-op).

``workflow_templates.tenant_id`` is NOT NULL (FK to tenants), so startup
seeds per active tenant via :func:`app.main` lifespan.  Tests may pass an
explicit ``tenant_id``.  Project provisioning still creates a per-tenant
``published`` clone when HR picks one via :func:`match_top_templates`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metrics import WorkflowTemplate

_OFFICIAL_TEMPLATES: tuple[dict, ...] = (
    # 1
    {
        "slug": "ao-product-launch",
        "title": "新品发布",
        "summary": "从需求收集到上线发布的产品全流程编排",
        "tags": ["产品", "市场", "运营"],
        "keywords": ["launch", "product", "release", "release-plan", "新功能", "上新", "发布"],
        "recommended_roles": ["pm", "frontend", "backend", "qa", "marketing", "delivery"],
        "ao_provider": "anthropic",
        "ao_model": "claude-3-5-sonnet",
        "quality_threshold": 85,
    },
    # 2
    {
        "slug": "ao-data-pipeline",
        "title": "数据管道搭建",
        "summary": "ETL/ELT 数据管道规划与上线",
        "tags": ["数据", "工程"],
        "keywords": ["pipeline", "etl", "数据", "data", "调度", "dag"],
        "recommended_roles": ["data_engineer", "backend", "qa"],
        "ao_provider": "openai",
        "ao_model": "gpt-4o",
        "quality_threshold": 85,
    },
    # 3
    {
        "slug": "ao-design-research",
        "title": "用户研究 + 设计走查",
        "summary": "访谈脚本 + 卡片分类 + 设计评审",
        "tags": ["用户研究", "设计"],
        "keywords": ["ux", "research", "research-script", "design-review", "用户研究"],
        "recommended_roles": ["design_researcher", "ux_designer", "pm"],
        "quality_threshold": 80,
    },
    # 4
    {
        "slug": "ao-internal-tooling",
        "title": "内部工具搭建",
        "summary": "为公司内部团队交付小工具的标准流程",
        "tags": ["内部工具", "工程"],
        "keywords": ["tool", "internal-tool", "自动化"],
        "recommended_roles": ["backend", "frontend", "qa"],
        "quality_threshold": 80,
    },
    # 5
    {
        "slug": "ao-customer-onboarding",
        "title": "新客 onboarding 流程",
        "summary": "从签单到首次落地的客户接入编排",
        "tags": ["客户成功", "运营"],
        "keywords": ["onboarding", "kickoff", "kick-off"],
        "recommended_roles": ["cs_manager", "integration_engineer", "pm"],
        "quality_threshold": 85,
    },
    # 6
    {
        "slug": "ao-incident-postmortem",
        "title": "事件复盘 + 改进清单",
        "summary": "故障复盘报告与改进项跟踪",
        "tags": ["运维", "质量"],
        "keywords": ["incident", "postmortem", "复盘", "事故"],
        "recommended_roles": ["sre", "qa", "pm"],
        "quality_threshold": 90,
    },
    # 7
    {
        "slug": "ao-financial-monthly-close",
        "title": "月度财务结算",
        "summary": "月末关账、报表、对账流程",
        "tags": ["财务"],
        "keywords": ["finance", "monthly", "month-end", "close", "结算"],
        "recommended_roles": ["finance_lead", "accountant", "data_engineer"],
        "quality_threshold": 95,
    },
    # 8
    {
        "slug": "ao-quarterly-okr-review",
        "title": "季度 OKR 复盘",
        "summary": "KR 进度采集、风险标注、行动项",
        "tags": ["运营", "管理"],
        "keywords": ["okr", "review", "季度", "复盘"],
        "recommended_roles": ["pm", "hr", "delivery"],
        "quality_threshold": 85,
    },
    # 9
    {
        "slug": "ao-compliance-audit",
        "title": "合规审计",
        "summary": "合规凭证收集、风险点对照、整改清单",
        "tags": ["合规", "法务"],
        "keywords": ["compliance", "audit", "audit-checklist", "合规"],
        "recommended_roles": ["compliance_officer", "legal", "qa"],
        "quality_threshold": 95,
    },
    # 10
    {
        "slug": "ao-legal-contract-draft",
        "title": "合同草案 + 法务审",
        "summary": "起草 → 内审 → 终稿合同的法务协作流",
        "tags": ["法务"],
        "keywords": ["contract", "legal-review", "合同"],
        "recommended_roles": ["legal", "paralegal", "delivery"],
        "quality_threshold": 95,
    },
    # 11
    {
        "slug": "ao-customer-feedback-triage",
        "title": "客户反馈聚合",
        "summary": "归类、分析、跟进工单化的客户反馈",
        "tags": ["客户成功", "产品"],
        "keywords": ["feedback", "triage", "客户反馈"],
        "recommended_roles": ["cs_manager", "pm", "data_analyst"],
        "quality_threshold": 80,
    },
    # 12
    {
        "slug": "ao-content-marketing",
        "title": "内容营销",
        "summary": "白皮书/案例/博客的多渠道分发",
        "tags": ["市场", "内容"],
        "keywords": ["content", "blog", "case-study", "whitepaper", "内容"],
        "recommended_roles": ["content_strategist", "writer", "designer"],
        "quality_threshold": 80,
    },
    # 13
    {
        "slug": "ao-security-incident",
        "title": "安全事件响应",
        "summary": "安全事件检测 → 通报 → 修复 → 复盘",
        "tags": ["安全", "运维"],
        "keywords": ["security", "incident", "siem", "漏洞"],
        "recommended_roles": ["security_engineer", "sre", "delivery"],
        "quality_threshold": 95,
    },
    # 14
    {
        "slug": "ao-supply-chain",
        "title": "供应链调度",
        "summary": "采购 → 入库 → 配送 → 结算的供应链编排",
        "tags": ["供应链"],
        "keywords": ["supply", "supply-chain", "采购"],
        "recommended_roles": ["supply_chain_manager", "finance_lead", "ops"],
        "quality_threshold": 85,
    },
    # 15
    {
        "slug": "ao-hiring-loop",
        "title": "招聘流程",
        "summary": "JD → 简历筛选 → 面试 → Offer 的招聘编排",
        "tags": ["HR"],
        "keywords": ["hiring", "recruiting", "招聘"],
        "recommended_roles": ["recruiter", "hrbp", "delivery"],
        "quality_threshold": 85,
    },
    # 16
    {
        "slug": "ao-pricing-experiment",
        "title": "定价实验",
        "summary": "调研 → 方案设计 → AB 跑 → 复盘",
        "tags": ["产品", "数据"],
        "keywords": ["pricing", "experiment", "ab-test", "定价"],
        "recommended_roles": ["pm", "data_analyst", "data_scientist"],
        "quality_threshold": 85,
    },
    # 17
    {
        "slug": "ao-mobile-app-release",
        "title": "移动端版本发布",
        "summary": "iOS/Android 双端版本灰度与回归",
        "tags": ["移动", "工程"],
        "keywords": ["mobile", "ios", "android", "release"],
        "recommended_roles": ["ios_dev", "android_dev", "qa", "delivery"],
        "quality_threshold": 85,
    },
    # 18
    {
        "slug": "ao-website-redesign",
        "title": "官网改版",
        "summary": "内容梳理 → 设计 → 上线官网改版流程",
        "tags": ["设计", "市场"],
        "keywords": ["website", "redesign", "marketing-site", "官网"],
        "recommended_roles": ["ux_designer", "frontend", "content_strategist"],
        "quality_threshold": 85,
    },
    # 19
    {
        "slug": "ao-customer-data-import",
        "title": "客户数据导入",
        "summary": "数据迁移 + 验证 + 回滚",
        "tags": ["数据", "工程"],
        "keywords": ["data-migration", "import", "数据迁移"],
        "recommended_roles": ["data_engineer", "backend", "qa"],
        "quality_threshold": 90,
    },
    # 20
    {
        "slug": "ao-marketing-campaign",
        "title": "营销活动编排",
        "summary": "活动方案 → 落地 → 复盘",
        "tags": ["市场", "运营"],
        "keywords": ["campaign", "marketing-campaign", "活动"],
        "recommended_roles": ["marketing_manager", "content_strategist", "designer"],
        "quality_threshold": 80,
    },
    # 21
    {
        "slug": "ao-customer-churn",
        "title": "流失客户挽救",
        "summary": "识别 → 触达 → 谈判 → 留存",
        "tags": ["客户成功"],
        "keywords": ["churn", "save", "流失"],
        "recommended_roles": ["cs_manager", "sales", "pm"],
        "quality_threshold": 85,
    },
    # 22
    {
        "slug": "ao-feature-experiment",
        "title": "功能 A/B 实验",
        "summary": "方案 → 实施 → 复盘",
        "tags": ["产品", "数据"],
        "keywords": ["experiment", "feature-flag", "ab-test", "实验"],
        "recommended_roles": ["pm", "data_scientist", "frontend"],
        "quality_threshold": 85,
    },
    # 23
    {
        "slug": "ao-data-quality-audit",
        "title": "数据质量审查",
        "summary": "指标定义、数据血缘、异常排查",
        "tags": ["数据", "质量"],
        "keywords": ["data-quality", "lineage", "dq"],
        "recommended_roles": ["data_engineer", "data_analyst", "qa"],
        "quality_threshold": 90,
    },
    # 24
    {
        "slug": "ao-vendor-onboarding",
        "title": "供应商引入",
        "summary": "资质收集 → 评估 → 合同 → 入库",
        "tags": ["供应链"],
        "keywords": ["vendor", "procurement", "供应商"],
        "recommended_roles": ["supply_chain_manager", "legal", "finance_lead"],
        "quality_threshold": 90,
    },
    # 25
    {
        "slug": "ao-brand-guidelines-refresh",
        "title": "品牌规范更新",
        "summary": "Logo/字体/口吻/资产更新",
        "tags": ["设计", "品牌"],
        "keywords": ["brand", "brand-guidelines", "品牌"],
        "recommended_roles": ["brand_manager", "designer", "content_strategist"],
        "quality_threshold": 80,
    },
    # 26
    {
        "slug": "ao-customer-research-survey",
        "title": "客户调研问卷",
        "summary": "问卷设计 → 分发 → 分析",
        "tags": ["客户成功", "数据"],
        "keywords": ["survey", "nps", "调研"],
        "recommended_roles": ["design_researcher", "data_analyst", "pm"],
        "quality_threshold": 80,
    },
    # 27
    {
        "slug": "ao-saas-renewal",
        "title": "续约 + 升级",
        "summary": "续约谈判 + 套餐升级",
        "tags": ["销售"],
        "keywords": ["renewal", "upsell", "续约"],
        "recommended_roles": ["sales", "cs_manager", "finance_lead"],
        "quality_threshold": 85,
    },
    # 28
    {
        "slug": "ao-platform-migration",
        "title": "平台迁移",
        "summary": "云平台、数据库、应用栈迁移",
        "tags": ["工程"],
        "keywords": ["migration", "platform-migration", "迁移"],
        "recommended_roles": ["sre", "backend", "qa"],
        "quality_threshold": 90,
    },
    # 29
    {
        "slug": "ao-skill-marketplace-onboarding",
        "title": "技能市场入驻",
        "summary": "技能上架 → 沙箱 → 审批",
        "tags": ["技能市场"],
        "keywords": ["skill-market", "skill-publish", "上架"],
        "recommended_roles": ["skill_curator", "security_reviewer", "delivery"],
        "quality_threshold": 85,
    },
    # 30
    {
        "slug": "ao-quarterly-board-pack",
        "title": "董事会季度包",
        "summary": "季度数据 + KPI + 风险汇总",
        "tags": ["管理"],
        "keywords": ["board", "board-pack", "季度汇报"],
        "recommended_roles": ["finance_lead", "pm", "delivery"],
        "quality_threshold": 95,
    },
)


assert len(_OFFICIAL_TEMPLATES) == 30, "expected exactly 30 official templates"


def official_templates_iter() -> Iterable[dict]:
    """Public iterator for tests; avoids leaking the mutable global."""
    return tuple(_OFFICIAL_TEMPLATES)


OFFICIAL_TEMPLATES = tuple(_OFFICIAL_TEMPLATES)


async def seed_official_workflow_templates(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
) -> int:
    """Idempotently upsert the 30 official rows.  Returns count of newly inserted rows.

    ``tenant_id`` is required for production seeds (column is NOT NULL).  The
    optional ``None`` default remains only for callers that already validate
    the target tenant; lifespan in ``main.py`` always passes a real id.
    """

    inserted = 0
    for spec in _OFFICIAL_TEMPLATES:
        existing = await session.scalar(
            select(WorkflowTemplate).where(
                WorkflowTemplate.tenant_id == tenant_id,
                WorkflowTemplate.slug == spec["slug"],
            )
        )
        if existing is not None:
            continue
        session.add(
            WorkflowTemplate(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                slug=spec["slug"],
                title=spec["title"],
                summary=spec["summary"],
                tags=list(spec["tags"]),
                keywords=list(spec["keywords"]),
                recommended_roles=list(spec["recommended_roles"]),
                quality_threshold=spec.get("quality_threshold", 85),
                ao_provider=spec.get("ao_provider"),
                ao_model=spec.get("ao_model"),
                status="published",
            )
        )
        inserted += 1
    if inserted:
        await session.flush()
        logger.info("[WorkflowCatalog] seeded {} official templates", inserted)
    return inserted


__all__ = [
    "OFFICIAL_TEMPLATES",
    "official_templates_iter",
    "seed_official_workflow_templates",
]
