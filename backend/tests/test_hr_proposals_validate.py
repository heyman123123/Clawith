import pytest

from app.services.hr_review_session_service import validate_team_building_proposals


def _role(name: str, *, leader: bool = False) -> dict:
    return {
        "key": name.lower().replace(" ", "_"),
        "name": name,
        "role_description": f"{name} responsibilities",
        "is_group_leader": leader,
    }


def _proposal(pid: str, label: str, leader_name: str = "Leader") -> dict:
    return {
        "id": pid,
        "label": label,
        "roles": [
            _role(leader_name, leader=True),
            _role("Engineer"),
        ],
    }


def test_validate_team_building_proposals_accepts_three_valid_proposals():
    proposals = [
        _proposal("p1", "精简 MVP"),
        _proposal("p2", "平衡型", "PM"),
        _proposal("p3", "全职能", "Founder"),
    ]
    normalized = validate_team_building_proposals(proposals)
    assert len(normalized) == 3
    assert all(len(item["roles"]) == 2 for item in normalized)
    assert all(sum(r["is_group_leader"] for r in item["roles"]) == 1 for item in normalized)


def test_validate_team_building_proposals_rejects_fewer_than_three():
    with pytest.raises(ValueError, match="at least 3"):
        validate_team_building_proposals([_proposal("p1", "A")])


def test_validate_team_building_proposals_rejects_duplicate_ids():
    proposals = [
        _proposal("same", "A"),
        _proposal("same", "B"),
        _proposal("p3", "C"),
    ]
    with pytest.raises(ValueError, match="duplicated"):
        validate_team_building_proposals(proposals)


def test_validate_team_building_proposals_rejects_missing_group_leader():
    bad = _proposal("p1", "Bad")
    bad["roles"][0]["is_group_leader"] = False
    with pytest.raises(ValueError, match="exactly one group leader"):
        validate_team_building_proposals([bad, _proposal("p2", "B"), _proposal("p3", "C")])
