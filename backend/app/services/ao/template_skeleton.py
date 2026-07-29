"""Generate a minimal AO YAML skeleton from official template metadata."""

from __future__ import annotations

from typing import Any

import yaml

from app.services.ao.workflow_composer import SYSTEM_ROLE_PATH_HINTS


def skeleton_yaml_for_roles(
    *,
    workflow_name: str,
    recommended_roles: list[str],
    provider: str = "openai",
    model: str = "clawith-gateway",
    concurrency: int = 2,
    agents_dir: str = "./agency-agents-zh",
) -> str:
    """Build clarify → execute×N → review → deliver YAML text for a template."""
    roles = [str(r).strip() for r in recommended_roles if str(r).strip()]
    if not roles:
        roles = ["executor"]
    execute_steps: list[dict[str, Any]] = []
    execute_ids: list[str] = []
    for index, role in enumerate(roles):
        step_id = f"execute_{role.replace('/', '-').replace('_', '-')}"[:64]
        execute_ids.append(step_id)
        execute_steps.append(
            {
                "id": step_id,
                "role": f"product/{role.replace('_', '-')}",
                "task": "{{plan}}",
                "depends_on": ["clarify"],
                "output": f"artifact_{index}",
            }
        )
    body = {
        "name": workflow_name,
        "agents_dir": agents_dir,
        "llm": {"provider": provider, "model": model},
        "concurrency": concurrency,
        "steps": [
            {
                "id": "clarify",
                "role": SYSTEM_ROLE_PATH_HINTS["scheduler"],
                "task": "把需求拆成可分发给各执行角色的步骤",
                "depends_on": [],
                "output": "plan",
            },
            *execute_steps,
            {
                "id": "review",
                "role": SYSTEM_ROLE_PATH_HINTS["quality"],
                "task": "对全部执行产物做质检，输出 score 0~100",
                "depends_on": execute_ids,
                "output": "review",
            },
            {
                "id": "deliver",
                "role": SYSTEM_ROLE_PATH_HINTS["delivery"],
                "task": "整理交付包并申请真人交付经理验收",
                "depends_on": ["review"],
                "output": "delivery_package",
            },
        ],
    }
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False)


__all__ = ["skeleton_yaml_for_roles"]
