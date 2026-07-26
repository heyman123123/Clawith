from app.services.project_team_builder import build_team_wakeup_message


def test_wakeup_message_uses_milestones_not_time_rhythm():
    message = build_team_wakeup_message({
        "project_name": "Demo",
        "requirements": "Ship MVP",
        "roles": [
            {
                "key": "leader",
                "name": "Leader",
                "role_description": "Own delivery",
                "is_group_leader": True,
            },
            {
                "key": "dev",
                "name": "Dev",
                "role_description": "Build features",
                "is_group_leader": False,
            },
        ],
    })
    assert "时间节奏" not in message
    assert "里程碑" in message
