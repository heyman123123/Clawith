from app.services.project_team_builder import build_team_wakeup_message


def test_wakeup_message_uses_milestones_not_time_rhythm():
    message = build_team_wakeup_message({
        "project_name": "Demo",
        "requirements": "Ship MVP",
        "roles": [
            {
                "key": "leader",
                "name": "Leader",
                "duties": "Own delivery",
                "soul": "# Leader\nYou own delivery.",
                "is_group_leader": True,
                "suggested_tools": ["group_write_workspace_file"],
                "suggested_permissions": {"scope_type": "company", "access_level": "use"},
            },
            {
                "key": "dev",
                "name": "Dev",
                "duties": "Build features",
                "soul": "# Dev\nYou build features.",
                "is_group_leader": False,
                "suggested_tools": ["group_write_workspace_file"],
                "suggested_permissions": {"scope_type": "company", "access_level": "use"},
            },
        ],
    })
    assert "时间节奏" not in message
    assert "里程碑" in message
