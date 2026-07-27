import pytest

from app.services.hr_review_session_service import validate_team_building_proposals


def _role(**overrides):
    base = {
        "key": "pm",
        "name": "项目经理",
        "duties": "统筹",
        "soul": "# PM\nYou lead the group.",
        "is_group_leader": True,
        "suggested_tools": ["group_write_workspace_file"],
        "suggested_permissions": {"scope_type": "company", "access_level": "use"},
    }
    base.update(overrides)
    return base


def test_proposals_require_three_full_roles():
    with pytest.raises(ValueError):
        validate_team_building_proposals([
            {"id": "p1", "label": "A", "card_summary": "x", "roles": [_role()]},
            {"id": "p2", "label": "B", "card_summary": "y", "roles": [_role(key="fe", name="FE", is_group_leader=False)]},
        ])


def test_role_missing_soul_rejected():
    bad = _role()
    del bad["soul"]
    with pytest.raises(ValueError, match="soul"):
        validate_team_building_proposals([
            {"id": f"p{i}", "label": str(i), "card_summary": "s", "roles": [bad if i == 1 else _role(key=f"r{i}", name=f"R{i}", is_group_leader=(i == 2))]}
            for i in range(1, 4)
        ])


def test_valid_three_proposals_normalized():
    proposals = [
        {
            "id": f"p{i}",
            "label": f"方案{i}",
            "card_summary": f"摘要{i}",
            "roles": [
                _role(key=f"lead{i}", name=f"Lead{i}", is_group_leader=True),
                _role(key=f"dev{i}", name=f"Dev{i}", is_group_leader=False),
            ],
        }
        for i in range(1, 4)
    ]
    out = validate_team_building_proposals(proposals)
    assert len(out) == 3
    assert out[0]["roles"][0]["soul"].startswith("#")
