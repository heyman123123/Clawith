"""Compose the initial AO workflow YAML + step skeleton for a confirmed project.

This is the P1.3 thin wrapper around ``AOClient.parse_workflow`` / ``validate``
/ ``plan``: once HR confirms a team plan and Clawith has already materialised
the four-power roles (scheduler / quality / delivery) plus the executor roles,
we still need to:

* emit a deterministic AO YAML file under ``settings.AO_WORKFLOWS_DIR``;
* record the cast (which executor role corresponds to which agent);
* persist a ``workflow_run_steps`` row per DAG node so P1.4 can resume from
  any step without re-parsing the YAML.

DAG shape (需求 §1.4 / gap-closure W2):

* ``clarify`` (scheduler)
* ``execute_<key>`` × N executor roles from the HR plan
* ``review`` (quality) depends on all execute steps
* ``deliver`` (delivery) depends on review

The composer never shells out to the AO CLI (that is ``AOClient``'s job) — it
only writes YAML text to disk. ``run_repository.create_run_row`` performs the
side-by-side DB write.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from loguru import logger

from app.config import get_settings
from app.models.agent import Agent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.project import ProjectWorkflow


settings = get_settings()


SYSTEM_ROLE_PATH_HINTS: dict[str, str] = {
    "scheduler": "product/project-scheduler",
    "quality": "quality/quality-reviewer",
    "delivery": "delivery/delivery-coordinator",
}

_FALLBACK_ROLE_PATH = "product/product-manager"
_POWER_SLOT_KEYS = frozenset({"scheduler", "quality", "delivery"})
_STEP_KEY_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True, slots=True)
class ComposeResult:
    """The YAML + run metadata produced by ``compose_initial_workflow``."""

    yaml_path: Path
    yaml_text: str
    step_count: int
    executor_role_path: str
    executor_agent_id: uuid.UUID | None


def _ao_role_path_for(agent: Agent, role_key: str) -> str:
    """Resolve an AO ``role_path`` for a Clawith Agent + ``role_key``."""
    hint = SYSTEM_ROLE_PATH_HINTS.get(role_key)
    if hint:
        return hint
    description = (agent.role_description or "").strip()
    if "/" in description:
        return description
    slug = role_key.strip().replace("_", "-")
    if slug:
        return f"product/{slug}"
    return _FALLBACK_ROLE_PATH


def _safe_step_key(raw: str, *, index: int) -> str:
    cleaned = _STEP_KEY_SAFE.sub("-", (raw or "").strip()).strip("-").lower()
    if not cleaned:
        cleaned = f"executor-{index}"
    return f"execute_{cleaned}"[:64]


def _iter_executor_roles(
    roles: list[dict],
    *,
    agent_ids: dict[str, uuid.UUID],
) -> list[tuple[str, str, uuid.UUID | None]]:
    """Return ``[(step_key, role_path, agent_id|None), ...]`` for executor slots.

    Skips power-slot keys. Falls back to a single placeholder executor when the
    plan has no business roles so the YAML remains kickable.
    """
    power_slots = set(_POWER_SLOT_KEYS)
    out: list[tuple[str, str, uuid.UUID | None]] = []
    for index, role in enumerate(roles):
        key = str(role.get("key") or "").strip()
        if not key or key in power_slots:
            continue
        step_key = _safe_step_key(key, index=index)
        role_path = f"product/{key.replace('_', '-')}"
        agent_id = agent_ids.get(key)
        if not isinstance(agent_id, uuid.UUID):
            agent_id = agent_ids.get(f"executor_{index}")
            if not isinstance(agent_id, uuid.UUID):
                agent_id = None
        out.append((step_key, role_path, agent_id))
    if not out:
        out.append(("execute_default", _FALLBACK_ROLE_PATH, None))
    return out


def build_dag_steps(
    roles: list[dict],
    *,
    agent_ids: dict[str, uuid.UUID],
) -> list[dict[str, Any]]:
    """Build the clarify → execute×N → review → deliver DAG step templates."""
    executors = _iter_executor_roles(roles, agent_ids=agent_ids)
    steps: list[dict[str, Any]] = [
        {
            "step_id": "clarify",
            "step_key": "clarify",
            "step_order": 0,
            "role_path": SYSTEM_ROLE_PATH_HINTS["scheduler"],
            "agent_role_key": "scheduler",
            "task": "把需求拆成可分发给各执行角色的步骤",
            "task_summary": "把需求拆成可分发给各执行角色的步骤",
            "output_variable": "plan",
            "output_var": "plan",
            "depends_on": [],
            "acceptance_text": "输出包含可执行的下游任务清单",
        }
    ]
    execute_keys: list[str] = []
    for index, (step_key, role_path, agent_id) in enumerate(executors):
        execute_keys.append(step_key)
        steps.append(
            {
                "step_id": step_key,
                "step_key": step_key,
                "step_order": 1 + index,
                "role_path": role_path,
                "agent_role_key": f"executor_{index}",
                "agent_id": agent_id,
                "task": "{{plan}}",
                "task_summary": f"执行角色步骤 {step_key}",
                "output_variable": f"artifact_{index}",
                "output_var": f"artifact_{index}",
                "depends_on": ["clarify"],
                "acceptance_text": "产出物落盘到工作流实例目录",
            }
        )
    review_order = 1 + len(executors)
    steps.append(
        {
            "step_id": "review",
            "step_key": "review",
            "step_order": review_order,
            "role_path": SYSTEM_ROLE_PATH_HINTS["quality"],
            "agent_role_key": "quality",
            "task": "对全部执行产物做质检，输出 score 0~100",
            "task_summary": "对全部执行产物做质检，输出 score 0~100",
            "output_variable": "review",
            "output_var": "review",
            "depends_on": list(execute_keys),
            "acceptance_text": "输出包含 score 与 feedback",
        }
    )
    steps.append(
        {
            "step_id": "deliver",
            "step_key": "deliver",
            "step_order": review_order + 1,
            "role_path": SYSTEM_ROLE_PATH_HINTS["delivery"],
            "agent_role_key": "delivery",
            "task": "整理交付包并申请真人交付经理验收",
            "task_summary": "整理交付包并申请真人交付经理验收",
            "output_variable": "delivery_package",
            "output_var": "delivery_package",
            "depends_on": ["review"],
            "acceptance_text": "交付包索引完整且可提交验收",
        }
    )
    return steps


def _render_yaml_text(
    *,
    workflow_name: str,
    agents_dir: str,
    provider: str,
    model: str,
    concurrency: int,
    dag_steps: list[dict[str, Any]],
) -> str:
    """Render the minimal AO YAML body Clawith consumes as the initial workflow."""
    steps_payload: list[dict] = []
    for template in dag_steps:
        steps_payload.append(
            {
                "id": template["step_id"],
                "role": template["role_path"],
                "task": template["task"],
                "depends_on": list(template["depends_on"]),
                "output": template["output_variable"],
            }
        )
    body = {
        "name": workflow_name,
        "agents_dir": agents_dir,
        "llm": {"provider": provider, "model": model},
        "concurrency": concurrency,
        "steps": steps_payload,
    }
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False)


def _yaml_target_path(workflow_id: uuid.UUID) -> Path:
    """Return ``<AO_WORKFLOWS_DIR>/<workflow_id>.yaml``; create parent if missing."""
    base = Path(settings.AO_WORKFLOWS_DIR or "")
    if not base:
        base = Path(settings.AO_HOME_DIR or ".") / "workflows"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{workflow_id}.yaml"


# Back-compat alias used by older tests / imports.
def _select_executor_role(
    roles: list[dict],
    *,
    agent_ids: dict[str, uuid.UUID],
) -> tuple[str, uuid.UUID | None]:
    executors = _iter_executor_roles(roles, agent_ids=agent_ids)
    step_key, role_path, agent_id = executors[0]
    del step_key
    return role_path, agent_id


async def compose_initial_workflow(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    agent_ids: dict[str, uuid.UUID],
    roles: list[dict],
) -> tuple[Path, dict]:
    """Compose the initial AO workflow YAML + a metadata envelope for a workflow."""
    del db  # reserved for AOAgentTemplate lookups.
    dag_steps = build_dag_steps(roles, agent_ids=agent_ids)
    executor_role_path = next(
        (s["role_path"] for s in dag_steps if str(s["step_id"]).startswith("execute")),
        _FALLBACK_ROLE_PATH,
    )
    first_executor_agent = next(
        (s.get("agent_id") for s in dag_steps if str(s["step_id"]).startswith("execute")),
        None,
    )
    yaml_text = _render_yaml_text(
        workflow_name=workflow.name,
        agents_dir=str(settings.AO_AGENTS_DIR or "./agency-agents-zh"),
        provider=str(settings.AO_PROVIDER or "openai"),
        model=str(settings.AO_MODEL or "clawith-gateway"),
        concurrency=int(settings.AO_CONCURRENCY),
        dag_steps=dag_steps,
    )
    yaml_path = _yaml_target_path(workflow.id)
    yaml_path.write_text(yaml_text, encoding="utf-8")
    metadata = {
        "yaml_text": yaml_text,
        "step_count": len(dag_steps),
        "executor_role_path": executor_role_path,
        "executor_agent_id": first_executor_agent,
        "executor_agent_ids": [
            str(s["agent_id"])
            for s in dag_steps
            if str(s["step_id"]).startswith("execute") and s.get("agent_id")
        ],
        "dag_steps": dag_steps,
        "step_role_paths": {s["step_id"]: s["role_path"] for s in dag_steps},
    }
    logger.info(
        "[AOComposer] Composed workflow {} at {} (steps={}, executors={})",
        workflow.id,
        yaml_path,
        metadata["step_count"],
        sum(1 for s in dag_steps if str(s["step_id"]).startswith("execute")),
    )
    return yaml_path, metadata


__all__ = [
    "ComposeResult",
    "SYSTEM_ROLE_PATH_HINTS",
    "_ao_role_path_for",
    "_select_executor_role",
    "build_dag_steps",
    "compose_initial_workflow",
]
