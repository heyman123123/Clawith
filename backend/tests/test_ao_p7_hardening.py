"""P7 hardening test suite (HARDENING + GAP-FILL).

Covers:
* Delivery two-dimension scoring (PASS/FAIL + exhaustion)
* WorkflowHumanReview lifecycle (open / resolve / 409)
* Security shell: SQL smell scan, tenant guard, safe subpath
* 30 official templates seed (idempotent + count)
* AssetCategory 8-bucket enum (legacy + canonical)
* MetricsCronState already covered in :mod:`tests.test_ao_p5_p6` —
  this file focuses on the new modules.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Pure scoring tests
# ---------------------------------------------------------------------------


def test_compute_final_score_pass_when_quality_dominates():
    from app.services.delivery_scoring import compute_final_score

    result = compute_final_score(quality=100.0, coverage=100.0, pass_threshold=90)
    assert result.final_score == 100
    assert result.passed is True
    assert result.exhausted is False


def test_compute_final_score_fail_when_quality_low():
    from app.services.delivery_scoring import compute_final_score

    result = compute_final_score(quality=40.0, coverage=80.0, pass_threshold=90)
    # 0.6*40 + 0.4*80 = 56
    assert result.final_score == 56
    assert result.passed is False
    assert result.exhausted is False  # first round is not the last
    assert result.round_no == 1


def test_compute_final_score_exhausts_at_third_round():
    from app.services.delivery_scoring import compute_final_score

    result = compute_final_score(quality=40.0, coverage=80.0, pass_threshold=90, round_no=3)
    assert result.exhausted is True


def test_compute_final_score_not_exhausted_at_second_round():
    from app.services.delivery_scoring import compute_final_score

    result = compute_final_score(quality=40.0, coverage=80.0, pass_threshold=90, round_no=2)
    assert result.exhausted is False


def test_compute_final_score_clamps_out_of_range():
    from app.services.delivery_scoring import compute_final_score

    result = compute_final_score(quality=200.0, coverage=-5.0)
    # 0.6*100 + 0.4*0 = 60
    assert result.final_score == 60


def test_compute_final_score_clamps_none_inputs():
    from app.services.delivery_scoring import compute_final_score

    result = compute_final_score(quality=None, coverage=None)  # type: ignore[arg-type]
    assert result.final_score == 0
    assert result.passed is False


def test_new_round_no_increments_without_capping():
    from app.services.delivery_scoring import MAX_ROUNDS, new_round_no

    assert new_round_no(None) == 1
    assert new_round_no(1) == 2
    assert new_round_no(2) == 3
    # Past the max: callers (API) must 409 — do NOT silently reuse round 3.
    assert new_round_no(3) == 4
    assert new_round_no(MAX_ROUNDS) == MAX_ROUNDS + 1


def test_attempt_label_render():
    from app.services.delivery_scoring import attempt_label

    assert attempt_label(1) == "第 1/3 轮验收"
    assert attempt_label(3) == "第 3/3 轮验收"


# ---------------------------------------------------------------------------
# 30 official templates — count + idempotency
# ---------------------------------------------------------------------------


def test_official_templates_catalog_has_exactly_30():
    from app.services.workflow_template_seeder import OFFICIAL_TEMPLATES

    assert len(OFFICIAL_TEMPLATES) == 30
    slugs = {t["slug"] for t in OFFICIAL_TEMPLATES}
    assert len(slugs) == 30  # unique slugs


def test_official_templates_have_quality_threshold():
    from app.services.workflow_template_seeder import OFFICIAL_TEMPLATES

    for tpl in OFFICIAL_TEMPLATES:
        assert "quality_threshold" in tpl
        assert 50 <= tpl["quality_threshold"] <= 100


@pytest.mark.asyncio
async def test_seed_official_templates_is_idempotent(monkeypatch):
    """Mock SQLAlchemy session to confirm second call is a no-op."""
    from app.services import workflow_template_seeder

    added: list[Any] = []

    class _StubTemplate:
        def __init__(self, **kw):
            self.kw = kw

    class _StubScalarResult:
        def __init__(self, value):
            self._value = value

        def scalar(self):
            return self._value

    class _StubSession:
        def __init__(self, existing):
            self._existing = existing

        async def scalar(self, _stmt):
            return self._existing.pop(0) if self._existing else None

        def add(self, row):
            added.append(row)

        async def flush(self):
            return None

    # First call: no rows exist → 30 inserted
    session_a = _StubSession([None] * 30)
    inserted = await workflow_template_seeder.seed_official_workflow_templates(
        session_a,  # type: ignore[arg-type]
        tenant_id=None,
    )
    assert inserted == 30
    assert len(added) == 30

    # Second call: all rows exist → 0 inserted
    session_b = _StubSession(list(added))
    inserted_again = await workflow_template_seeder.seed_official_workflow_templates(
        session_b,  # type: ignore[arg-type]
        tenant_id=None,
    )
    assert inserted_again == 0


# ---------------------------------------------------------------------------
# 8-bucket asset enforcer
# ---------------------------------------------------------------------------


def test_asset_category_eight_buckets():
    from app.services.ao.asset_directory_enforcer import (
        AssetCategory,
        canonical_directory_set,
    )

    dirs = canonical_directory_set()
    assert len(dirs) == 8
    assert "00-工作流定义" in dirs
    assert "07-历史迭代" in dirs
    # The 8 directories match the on-disk names declared in AssetCategory
    assert {m.value for m in AssetCategory} == dirs


def test_bucket_for_legacy_category():
    from app.services.ao.asset_directory_enforcer import (
        AssetCategory,
        bucket_for,
        is_valid_category,
    )

    assert bucket_for("requirement") is AssetCategory.WORKFLOW_DEFINITION
    assert bucket_for("execution") is AssetCategory.STEP_OUTPUT
    assert bucket_for("quality") is AssetCategory.QUALITY_CONTROL
    assert bucket_for("delivery") is AssetCategory.DELIVERY_REVIEW
    assert is_valid_category("00-工作流定义") is True
    assert is_valid_category("quality") is True
    assert is_valid_category("totally-unknown") is False


def test_bucket_for_unknown_raises():
    from app.services.ao.asset_directory_enforcer import bucket_for

    with pytest.raises(ValueError):
        bucket_for("not-a-category")


# ---------------------------------------------------------------------------
# Security shell
# ---------------------------------------------------------------------------


def test_scan_for_sql_smells_clean_input():
    from app.services.security_shell import scan_for_sql_smells

    result = scan_for_sql_smells("Normal text 123")
    assert result.safe is True
    assert not result.findings


def test_scan_for_sql_smells_flags_union_select():
    from app.services.security_shell import scan_for_sql_smells

    result = scan_for_sql_smells("SELECT * FROM users UNION SELECT password FROM admin")
    assert result.safe is False
    assert any("union" in f.lower() for f in result.findings)


def test_scan_for_sql_smells_handles_none():
    from app.services.security_shell import scan_for_sql_smells

    result = scan_for_sql_smells(None)
    assert result.safe is True
    assert not result.findings


def test_assert_tenant_owns_blocks_cross_tenant():
    from app.services.security_shell import assert_tenant_owns

    assert_tenant_owns(
        actor_tenant_id="t-1",
        record_tenant_id="t-1",
        context="read",
    )
    with pytest.raises(PermissionError):
        assert_tenant_owns(
            actor_tenant_id="t-1",
            record_tenant_id="t-2",
            context="read",
        )


def test_safe_subpath_rejects_traversal():
    from app.services.security_shell import safe_subpath

    assert safe_subpath("a", "b") == "a/b"
    with pytest.raises(ValueError):
        safe_subpath("..", "etc", "passwd")
    with pytest.raises(ValueError):
        safe_subpath("a/b")
    with pytest.raises(ValueError):
        safe_subpath("")


def test_placeholder_encrypt_is_deterministic():
    from app.services.security_shell import placeholder_encrypt

    a = placeholder_encrypt("hello")
    b = placeholder_encrypt("hello")
    assert a == b
    c = placeholder_encrypt("world")
    assert a != c


def test_enumerate_audit_categories_dedupes_preserves_order():
    from app.services.security_shell import enumerate_audit_categories

    out = enumerate_audit_categories(["a", "b", "a", "c", "b"])
    assert out == ("a", "b", "c")


# ---------------------------------------------------------------------------
# Delivery review API lifecycle (no live DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivery_round_escalates_to_shareholder_on_three_failures(monkeypatch):
    """Round 3 failure opens shareholder review; a 4th attempt returns 409."""

    from fastapi import HTTPException

    from app.api import delivery_review as dr
    from app.services.delivery_scoring import MAX_ROUNDS

    class _StubScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class _StubResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _StubScalars(self._rows)

    class _StubSession:
        def __init__(self, prior_rows=None):
            self.added: list[Any] = []
            self.commits = 0
            self._prior = list(prior_rows or [])

        async def execute(self, _stmt):
            return _StubResult(self._prior)

        def add(self, row):
            self.added.append(row)

        async def commit(self):
            self.commits += 1

        async def refresh(self, row):
            return None

    class _StubUser:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()

    body = dr.DeliveryRoundIn(
        quality_score=10.0,
        coverage_score=10.0,
        coverage_notes="missing feature X",
        quality_notes="no tests",
        rectification_items=[{"kind": "coverage", "summary": "missing X"}],
    )

    # Round 1 — reject, no escalation
    session = _StubSession()
    response = await dr.submit_delivery_round(
        workflow_id=uuid.uuid4(),
        body=body,
        current_user=_StubUser(),  # type: ignore[arg-type]
        db=session,  # type: ignore[arg-type]
    )
    assert response["decision"] == "rejected"
    assert not any(
        getattr(row, "kind", None) == "shareholder_decision" for row in session.added
    )

    # Round 3 (previous=2) — reject + open shareholder review
    session3 = _StubSession(prior_rows=[SimpleNamespace(round_no=MAX_ROUNDS - 1)])
    response3 = await dr.submit_delivery_round(
        workflow_id=uuid.uuid4(),
        body=body,
        current_user=_StubUser(),  # type: ignore[arg-type]
        db=session3,  # type: ignore[arg-type]
    )
    assert response3["decision"] == "rejected"
    assert response3["round_no"] == MAX_ROUNDS
    assert any(
        getattr(row, "kind", None) == "shareholder_decision" for row in session3.added
    )

    # Round 4 attempt (previous=3) — hard stop
    session4 = _StubSession(prior_rows=[SimpleNamespace(round_no=MAX_ROUNDS)])
    with pytest.raises(HTTPException) as exc_info:
        await dr.submit_delivery_round(
            workflow_id=uuid.uuid4(),
            body=body,
            current_user=_StubUser(),  # type: ignore[arg-type]
            db=session4,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_human_review_resolve_lifecycle(monkeypatch):
    from app.api import delivery_review as dr
    from app.models.delivery_review import WorkflowHumanReview

    target_id = uuid.uuid4()
    target = WorkflowHumanReview(
        id=target_id,
        tenant_id=uuid.uuid4(),
        kind="high_risk_skill",
        status="open",
        payload={"skill": "dangerous-shell"},
    )

    class _StubScalars:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _StubResult:
        def __init__(self, row):
            self._row = row

        def scalars(self):
            return _StubScalars(self._row)

    class _StubSession:
        def __init__(self):
            self.commits = 0

        async def execute(self, _stmt):
            return _StubResult(target)

        async def commit(self):
            self.commits += 1

        async def refresh(self, row):
            row.status = "approved"
            row.resolved_at = row.resolved_at or "now"

    class _StubUser:
        id = uuid.uuid4()
        tenant_id = target.tenant_id

    body = dr.HumanReviewResolveIn(decision="approved", notes="all good")
    response = await dr.resolve_human_review(
        review_id=target_id,
        body=body,
        current_user=_StubUser(),  # type: ignore[arg-type]
        db=_StubSession(),  # type: ignore[arg-type]
    )
    assert response["id"] == str(target_id)


# ---------------------------------------------------------------------------
# Asset category directory enforcer — debug hook install
# ---------------------------------------------------------------------------


def test_install_dir_assert_hook_is_noop_when_env_unset(monkeypatch):
    monkeypatch.delenv("CLAWITH_ASSET_DEBUG", raising=False)
    from app.services.ao import asset_directory_enforcer

    # Calling with the env var unset must be a no-op and not raise
    asset_directory_enforcer.install_dir_assert_hook()


def test_install_dir_assert_hook_when_enabled(monkeypatch):
    monkeypatch.setenv("CLAWITH_ASSET_DEBUG", "1")
    from app.services.ao import asset_directory_enforcer

    # Reset internal flag so a re-run still installs
    asset_directory_enforcer._ASSET_DEBUG_HOOK_INSTALLED = False
    asset_directory_enforcer.install_dir_assert_hook()
    # Re-installation is a no-op (idempotent)
    asset_directory_enforcer.install_dir_assert_hook()


# ---------------------------------------------------------------------------
# 20-concurrent proposal generation soak (P7 perf)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_top_templates_handles_20_concurrent_calls():
    """需求 §5 — 20 并发 proposal 生成不互相阻塞."""
    from app.services.workflow_metrics import TemplateMatch, rank_top_n
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

    queries = [
        "客户成功续约和流失挽救",
        "新功能上线配合 A/B 实验",
        "财务月结 + 数据对账",
        "招聘流程",
        "法务合同审",
        "品牌规范更新",
        "官网改版",
        "数据迁移到新仓库",
        "安全事件响应",
        "供应商资质审查",
        "客户反馈分类",
        "OKR 季度复盘",
        "续约谈判",
        "定价实验",
        "移动端版本发布",
        "客户调研问卷",
        "内部工具搭建",
        "技能上架",
        "董事会季度包",
        "新客 onboarding",
    ]

    async def one(query: str) -> list[TemplateMatch]:
        # rank_top_n is sync; run in a thread so the gather actually
        # exercises the asyncio scheduler.  20 callers, no deadlock.
        return await asyncio.to_thread(rank_top_n, query, candidates, top_n=3)

    results = await asyncio.gather(*[one(q) for q in queries])
    assert len(results) == 20
    assert all(isinstance(r, list) for r in results)
    # Each query that has overlap should return at least one candidate
    matched_count = sum(1 for r in results if r)
    assert matched_count >= 10  # most queries should match at least 1 template

