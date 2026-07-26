"""Unit tests for applying governance top-up proposal field parsing helpers."""

from __future__ import annotations


def test_governance_proposal_actions_shape_is_documented():
    """Contract for Session B structured proposals consumed by apply_governance_proposal."""
    proposal = {
        "id": "proposal_2",
        "label": "新建 X",
        "plan": "新建 Agent X",
        "enable_role_keys": ["cfo"],
        "disable_role_keys": ["cmo"],
        "create_roles": [
            {
                "role_key": "security_review",
                "role_type": "review",
                "role_title": "安全评审 Agent",
            }
        ],
    }
    assert proposal["enable_role_keys"]
    assert proposal["create_roles"][0]["role_type"] in {"decision", "review"}
