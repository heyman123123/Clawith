"""Company-scoped role templates and their enterprise knowledge-base index."""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentTemplate
from app.models.tenant import Tenant
from app.services.storage_runtime import get_storage_backend, tenant_storage_key

logger = logging.getLogger(__name__)

_ROLE_LIBRARY_ROOT = "knowledge_base/role-library"
_README_PATH = f"{_ROLE_LIBRARY_ROOT}/README.md"
_SOURCE_PATH = f"{_ROLE_LIBRARY_ROOT}/SOURCE.md"
_CATALOG_PATH = f"{_ROLE_LIBRARY_ROOT}/catalog.generated.json"
_CUSTOM_ROLE_ROOT = f"{_ROLE_LIBRARY_ROOT}/custom"


def visible_role_templates(tenant_id: uuid.UUID):
    """SQL predicate for templates a company is allowed to use.

    Builtin templates are shared; all other templates are tenant-scoped. A
    migration assigns legacy custom templates to their creator's company,
    preventing company-specific personas from appearing in another tenant.
    """
    return or_(
        AgentTemplate.is_builtin.is_(True),
        AgentTemplate.tenant_id == tenant_id,
    )


def _role_soul(*, name: str, role_description: str, responsibility: str) -> str:
    return "\n".join(
        [
            f"# Soul — {name}",
            "",
            "## Identity",
            f"- **Role**: {name}",
            f"- **Positioning**: {role_description}",
            "",
            "## Responsibility",
            responsibility.strip(),
            "",
            "## Work Style",
            "- 先澄清目标、范围、依赖和验收标准，再开始执行。",
            "- 输出可复核的结论、证据和下一步，而不是只报告已完成。",
            "- 在团队中接受群主编排；发现风险或阻塞时及时公开同步。",
            "",
            "## Boundaries",
            "- 不擅自改变团队目标、跨越职责边界或代表人类作最终决策。",
            "- 涉及外部沟通、发布、费用或敏感信息时，先按团队流程升级。",
        ]
    )


def _custom_role_document(template: AgentTemplate) -> str:
    return "\n".join(
        [
            f"# {template.name}",
            "",
            "- 来源：一句话组队自动沉淀的公司角色",
            f"- 角色库 ID：{template.id}",
            f"- 分类：{template.category}",
            "",
            "## 角色说明",
            template.description or "未填写",
            "",
            "## 人格模板",
            template.soul_template or "未填写",
        ]
    )


def _catalog_payload(templates: list[AgentTemplate]) -> str:
    categories: dict[str, list[dict[str, object]]] = defaultdict(list)
    for template in templates:
        categories[template.category or "general"].append(
            {
                "id": str(template.id),
                "name": template.name,
                "description": template.description or "",
                "capabilities": list(template.capability_bullets or []),
                "source": "builtin" if template.is_builtin else "company",
            }
        )
    return json.dumps(
        {
            "title": "Clawith 默认角色库目录",
            "generated": True,
            "role_count": len(templates),
            "categories": dict(categories),
        },
        ensure_ascii=False,
        indent=2,
    )


async def ensure_company_role_library(db: AsyncSession, *, tenant_id: uuid.UUID) -> list[AgentTemplate]:
    """Ensure the company can browse a current, non-destructive role index.

    The README is created only once so company edits are never overwritten.
    The generated catalog is deliberately system-owned and refreshed from the
    role library; full builtin personas remain authoritative in AgentTemplate.
    """
    result = await db.execute(
        select(AgentTemplate)
        .where(visible_role_templates(tenant_id))
        .order_by(AgentTemplate.is_builtin.desc(), AgentTemplate.category, AgentTemplate.name)
    )
    templates = list(result.scalars().all())
    storage = get_storage_backend()
    readme_key = tenant_storage_key(tenant_id, _README_PATH)
    if not await storage.exists(readme_key):
        await storage.write_text(
            readme_key,
            "# 默认公司角色库\n\n"
            "这里是公司可复用的 AI 角色目录。内置角色来自已安装的人格模板；"
            "由一句话组队新建的角色会同时出现在 `custom/` 中。\n\n"
            "- `catalog.generated.json`：系统维护的可检索角色索引，请勿手工编辑。\n"
            "- `custom/`：公司自行沉淀的角色人格说明。\n"
            "- 完整的内置人格模板以平台角色库为准，创建 Agent 时会自动注入。\n",
        )
    source_key = tenant_storage_key(tenant_id, _SOURCE_PATH)
    if not await storage.exists(source_key):
        await storage.write_text(
            source_key,
            "# 角色库来源\n\n"
            "内置角色人格模板包含来自 `jnMetaCode/agency-agents-zh` 的角色，"
            "按其 MIT License 使用；上游版本：`2ecfabf8e944ccdfed63ad8c44d5241290af6977`。\n\n"
            "完整授权文本随平台源代码保存在 "
            "`backend/agent_templates/AGENCY_AGENTS_ZH_LICENSE.md`。\n",
        )
    await storage.write_text(tenant_storage_key(tenant_id, _CATALOG_PATH), _catalog_payload(templates))
    for template in templates:
        if template.is_builtin:
            continue
        document_key = tenant_storage_key(tenant_id, f"{_CUSTOM_ROLE_ROOT}/{template.id}.md")
        if not await storage.exists(document_key):
            await storage.write_text(document_key, _custom_role_document(template))
    return templates


async def seed_company_role_libraries() -> None:
    """Create the default role-library index for every existing company."""
    from app.database import async_session

    async with async_session() as db:
        result = await db.execute(select(Tenant.id))
        for tenant_id in result.scalars().all():
            try:
                await ensure_company_role_library(db, tenant_id=tenant_id)
            except Exception:
                logger.exception("Unable to seed company role knowledge base for tenant %s", tenant_id)


async def get_or_create_company_role_template(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    name: str,
    role_description: str,
    responsibility: str,
) -> AgentTemplate:
    """Return the company role matching a newly planned role, creating it once."""
    existing = await db.execute(
        select(AgentTemplate).where(
            AgentTemplate.tenant_id == tenant_id,
            AgentTemplate.name == name.strip(),
        )
    )
    template = existing.scalar_one_or_none()
    if template is None:
        description = role_description.strip()
        template = AgentTemplate(
            name=name.strip(),
            description=description,
            icon="🤖",
            category="company-custom",
            soul_template=_role_soul(
                name=name.strip(), role_description=description, responsibility=responsibility
            ),
            capability_bullets=[description[:120], responsibility.strip()[:120]],
            default_skills=[],
            default_mcp_servers=[],
            default_autonomy_policy={},
            is_builtin=False,
            tenant_id=tenant_id,
            created_by=creator_id,
        )
        db.add(template)
        await db.flush()
    try:
        await ensure_company_role_library(db, tenant_id=tenant_id)
    except Exception:
        # The database role is authoritative. A transient object-storage issue
        # must not leave a confirmed team unable to start; the next planning
        # request will repair the generated index.
        logger.exception("Unable to refresh company role knowledge base for tenant %s", tenant_id)
    return template
