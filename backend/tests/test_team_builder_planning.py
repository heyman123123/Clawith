"""Pure contract tests for the pre-provisioning team plan."""

from __future__ import annotations

import uuid

import pytest

from app.services.team_builder.errors import TeamBuilderError
from app.services.team_builder.planning import fallback_team_plan, validate_team_plan


def test_fallback_plan_has_one_new_leader_and_public_delegation() -> None:
    plan = fallback_team_plan("准备一个产品发布")

    leaders = [member for member in plan.members if member.is_leader]
    assert len(leaders) == 1
    assert leaders[0].source == "new"
    assert plan.delegations[0].from_member_key == leaders[0].member_key


def test_team_plan_rejects_multiple_leaders() -> None:
    agent_id = str(uuid.uuid4())
    payload = {
        "group_name": "Launch",
        "goal": "Ship",
        "assumptions": [],
        "phases": ["Plan"],
        "members": [
            {
                "member_key": "leader_one",
                "name": "Leader One",
                "role_description": "Lead",
                "responsibility": "Coordinate",
                "source": "existing",
                "existing_agent_id": agent_id,
                "template_id": None,
                "skill_ids": [],
                "is_leader": True,
            },
            {
                "member_key": "leader_two",
                "name": "Leader Two",
                "role_description": "Lead",
                "responsibility": "Coordinate",
                "source": "existing",
                "existing_agent_id": str(uuid.uuid4()),
                "template_id": None,
                "skill_ids": [],
                "is_leader": True,
            },
        ],
        "delegations": [],
    }

    with pytest.raises(TeamBuilderError, match="exactly one member"):
        validate_team_plan(payload)


def test_team_plan_rejects_existing_member_without_agent_identity() -> None:
    payload = fallback_team_plan("Prepare launch").model_dump(mode="json")
    payload["members"][0]["source"] = "existing"

    with pytest.raises(TeamBuilderError, match="existing members require"):
        validate_team_plan(payload)
