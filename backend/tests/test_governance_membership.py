import uuid
from types import SimpleNamespace

import pytest

from app.services.governance_membership import _select_role_keys, select_decision_group_members


def test_select_role_keys_picks_decision_and_review_limits():
    rows = [
        SimpleNamespace(role_key="ceo", role_type="decision", is_default_enabled=True),
        SimpleNamespace(role_key="cto", role_type="decision", is_default_enabled=True),
        SimpleNamespace(role_key="coo", role_type="decision", is_default_enabled=True),
        SimpleNamespace(role_key="cfo", role_type="decision", is_default_enabled=False),
        SimpleNamespace(role_key="product_review", role_type="review", is_default_enabled=True),
        SimpleNamespace(role_key="tech_architecture", role_type="review", is_default_enabled=True),
        SimpleNamespace(role_key="legal_compliance", role_type="review", is_default_enabled=True),
        SimpleNamespace(role_key="data_ai", role_type="review", is_default_enabled=True),
        SimpleNamespace(role_key="finance_roi", role_type="review", is_default_enabled=False),
    ]
    decision_keys = _select_role_keys(rows, role_type="decision", priority=("ceo", "cto", "coo"), limit=2)
    review_keys = _select_role_keys(
        rows,
        role_type="review",
        priority=("product_review", "tech_architecture", "legal_compliance", "data_ai"),
        limit=3,
    )
    assert decision_keys == ["ceo", "cto"]
    assert review_keys == ["product_review", "tech_architecture", "legal_compliance"]


@pytest.mark.asyncio
async def test_select_decision_group_members_always_includes_leader():
    leader = SimpleNamespace(id=uuid.uuid4(), type="agent", ref_id=uuid.uuid4(), display_name="Leader")

    class _Result:
        def all(self):
            return []

    class _Session:
        async def execute(self, _query):
            return _Result()

        async def scalar(self, _query):
            return None

        def add(self, _obj):
            return None

        async def flush(self):
            return None

    members = await select_decision_group_members(
        _Session(),
        tenant_id=uuid.uuid4(),
        leader_participant=leader,
    )
    assert members == [leader]
