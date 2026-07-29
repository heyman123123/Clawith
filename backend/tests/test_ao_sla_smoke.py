"""Minimal SLA smoke gates for AO gap closure (需求 §5 / §8.8).

Marked ``@pytest.mark.sla`` so slow CI hosts can skip with
``-m "not sla"`` while default runs still enforce the budgets.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest

from app.services.ao.quality_rules import evaluate_output
from app.services.ao.workflow_composer import build_dag_steps


pytestmark = pytest.mark.sla


def test_rule_quality_check_under_10s() -> None:
    """Pure rule engine step check must finish well under the 10s SLA."""
    text = (
        "# Delivery notes\n\n"
        "- Acceptance criteria listed\n"
        "- Risks and mitigations\n"
        "- Next actions for stakeholders\n"
    ) * 40
    started = time.perf_counter()
    for _ in range(200):
        verdict = evaluate_output(step_id="deliver", output_text=text, rules=None)
        assert verdict.score >= 0
    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, f"rule quality smoke took {elapsed:.3f}s"


def test_compose_dag_under_5s() -> None:
    """Compose / DAG seeding path (proxy for provisioning hot path) < 5s."""
    roles = [
        {"key": "scheduler", "role_path": "system/scheduler"},
        {"key": "frontend", "role_path": "product/frontend"},
        {"key": "backend", "role_path": "product/backend"},
        {"key": "qa", "role_path": "product/qa"},
        {"key": "quality", "role_path": "system/quality"},
        {"key": "delivery", "role_path": "system/delivery"},
    ]
    agent_ids = {r["key"]: uuid.uuid4() for r in roles}
    started = time.perf_counter()
    for _ in range(500):
        steps = build_dag_steps(roles, agent_ids=agent_ids)
        assert len(steps) >= 4
        assert steps[0]["step_key"] == "clarify"
        assert steps[-1]["step_key"] == "deliver"
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"compose DAG smoke took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_match_top_templates_20_concurrent_under_budget() -> None:
    """Keep the P7 20-concurrent template match gate under a soft 5s budget."""
    from app.services.workflow_metrics import rank_top_n
    from app.services.workflow_template_seeder import OFFICIAL_TEMPLATES

    candidates = [
        SimpleNamespace(
            id=idx,
            slug=t["slug"],
            title=t["title"],
            summary=t["summary"],
            tags=t["tags"],
            keywords=t["keywords"],
        )
        for idx, t in enumerate(OFFICIAL_TEMPLATES)
    ]
    queries = [f"query-{i} 客户成功 续约 招聘 财务" for i in range(20)]

    async def one(query: str):
        return await asyncio.to_thread(rank_top_n, query, candidates, top_n=3)

    started = time.perf_counter()
    results = await asyncio.gather(*[one(q) for q in queries])
    elapsed = time.perf_counter() - started
    assert len(results) == 20
    assert elapsed < 5.0, f"20-concurrent match took {elapsed:.3f}s"
