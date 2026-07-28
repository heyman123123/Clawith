"""Default regression harness fixtures (P4).

Workflow roles — scheduler, quality, delivery, executor — get a small
frozen prompt bundle as soon as they exist so :mod:`regression_harness`
has something to score against. Callers (project provisioning, role
seeders, manual admin UIs) invoke :func:`ensure_default_harness_fixtures`
and the function is idempotent — fixtures are matched by ``agent_id +
fixture_role + title`` so re-seeding never duplicates rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from loguru import logger
from sqlalchemy import select

from app.models.evolution import AgentHarnessFixture

_DEFAULT_FIXTURES: dict[str, list[dict]] = {
    "scheduler": [
        {
            "title": "生成周计划摘要",
            "task_summary": "把本周 OKR 进度用 1 段中文总结，包含高优目标与风险。",
            "expected_keywords": ["周计划", "OKR", "高优"],
        },
        {
            "title": "按依赖顺序重启步骤",
            "task_summary": "列出步骤之间的依赖并解释为什么会卡住。",
            "expected_keywords": ["依赖", "步骤", "原因"],
        },
    ],
    "quality": [
        {
            "title": "质控反馈草稿",
            "task_summary": "为通过的步骤写一段质控反馈，提到分数与改进点。",
            "expected_keywords": ["质控", "反馈", "分数"],
        },
        {
            "title": "拒答风险升级",
            "task_summary": "如果打分低于阈值，请列出风险点、升级对象、缓解建议。",
            "expected_keywords": ["风险", "升级", "建议"],
        },
    ],
    "delivery": [
        {
            "title": "交付件清单",
            "task_summary": "列出可向用户交付的文件 URL 与一句话简介。",
            "expected_keywords": ["交付", "URL", "简介"],
        },
    ],
}

_GENERIC_FIXTURES: list[dict] = [
    {
        "title": "工作小结",
        "task_summary": "把你刚才完成的工作总结成 200 字以内的小结，标注价值与下一步。",
        "expected_keywords": ["总结", "下一步"],
    },
    {
        "title": "失败回滚说明",
        "task_summary": "若步骤失败，请给出回滚方案与影响面。",
        "expected_keywords": ["回滚", "影响", "方案"],
    },
]


async def ensure_default_harness_fixtures(
    db,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    role_key: str,
) -> list[AgentHarnessFixture]:
    """Make sure the agent has at least one fixture; never duplicate rows."""
    bundle = list(_DEFAULT_FIXTURES.get(role_key.lower(), ())) + list(_GENERIC_FIXTURES)
    bundle = [
        {**fixture, "fixture_role": role_key, "tenant_id": tenant_id, "agent_id": agent_id}
        for fixture in bundle
    ]
    existing = (
        await db.execute(
            select(AgentHarnessFixture).where(AgentHarnessFixture.agent_id == agent_id)
        )
    ).scalars().all()
    existing_keys = {(f.fixture_role, f.title) for f in existing}

    created: list[AgentHarnessFixture] = []
    for payload in bundle:
        key = (payload["fixture_role"], payload["title"])
        if key in existing_keys:
            continue
        fixture = AgentHarnessFixture(
            id=uuid.uuid4(),
            tenant_id=payload["tenant_id"],
            agent_id=payload["agent_id"],
            fixture_role=payload["fixture_role"],
            kind="role_qa",
            title=payload["title"],
            task_summary=payload["task_summary"],
            acceptance_text=None,
            expected_keywords=payload["expected_keywords"],
            rubric=None,
            weight=1,
            enabled=True,
        )
        db.add(fixture)
        created.append(fixture)
    if created:
        await db.flush()
        logger.info(
            "[HarnessSeeder] inserted {} fixtures for agent={} role={}",
            len(created),
            agent_id,
            role_key,
        )
    return created


def default_fixture_payloads_for(role_key: str) -> Iterable[dict]:
    """Return the seed payload dicts without persisting them.

    Used by tests so the test-file assertions and the seeder stay in
    sync without round-tripping through the DB.
    """
    yield from _DEFAULT_FIXTURES.get(role_key.lower(), ())
    yield from _GENERIC_FIXTURES


__all__ = [
    "default_fixture_payloads_for",
    "ensure_default_harness_fixtures",
]
