"""Compose the initial AO workflow YAML + step skeleton for a confirmed project.

This is the P1.3 thin wrapper around ``AOClient.parse_workflow`` / ``validate``
/ ``plan``: once HR confirms a team plan and Clawith has already materialised
the four-power roles (scheduler / quality / delivery) plus the executor roles,
we still need to:

* emit a deterministic AO YAML file under ``settings.AO_WORKFLOWS_DIR``;
* record the cast (which executor role corresponds to which agent);
* persist a ``workflow_run_steps`` row per DAG node so P1.4 can resume from
  any step without re-parsing the YAML.

The composer never shells out to the AO CLI (that is ``AOClient``'s job) — it
only writes YAML text to disk. ``run_repository.create_run_row`` performs the
side-by-side DB write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from loguru import logger

from app.config import get_settings
from app.models.agent import Agent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.project import ProjectWorkflow


settings = get_settings()


# 必备位：在 Clawith 已保证存在 scheduler / quality / delivery 三个角色，
# 这里给出 AO 默认 role_path 候选。优先级：role_description（workflow.xxx）
# → builtin 的 role_description → 兜底 "product/product-manager"。
SYSTEM_ROLE_PATH_HINTS: dict[str, str] = {
    "scheduler": "product/project-scheduler",
    "quality": "quality/quality-reviewer",
    "delivery": "delivery/delivery-coordinator",
}

# 兜底 role_path：AO 角色库找不到精确匹配时，调度/质控/交付位都会回落到
# 这里，保证建群阶段不会因为缺角色而阻塞。
_FALLBACK_ROLE_PATH = "product/product-manager"

# 默认三步 DAG（clarify → execute → review）。每一步由 step_id、
# role_path、task 模板、依赖、output_variable、acceptance_text 组成。
# executor 的 role_path 由 ``_ao_role_path_for(agent, "executor_<i>")``
# 在运行时决定；其他两步的 role_path 在本模块顶层写死。
_DEFAULT_DAG_TEMPLATE: tuple[dict, ...] = (
    {
        "step_id": "clarify",
        "step_order": 0,
        "role_path": SYSTEM_ROLE_PATH_HINTS["scheduler"],
        "agent_role_key": "scheduler",
        "task": "把需求拆成 3~5 个执行步骤",
        "output_variable": "plan",
        "depends_on": [],
        "acceptance_text": "输出包含可执行的下游任务清单",
    },
    {
        # executor 的 role_path 由调用方在 ``compose_initial_workflow`` 内
        # 依据 ``roles`` 中第一个非必备位 Agent 决定；占位符 ``<EXECUTOR_ROLE>``
        # 在渲染时替换。depends_on 与 output 固定。
        "step_id": "execute",
        "step_order": 1,
        "role_path": "<EXECUTOR_ROLE>",
        "agent_role_key": "executor_0",
        "task": "{{plan}}",
        "output_variable": "artifact",
        "depends_on": ["clarify"],
        "acceptance_text": "产出物落盘到工作流实例目录",
    },
    {
        "step_id": "review",
        "step_order": 2,
        "role_path": SYSTEM_ROLE_PATH_HINTS["quality"],
        "agent_role_key": "quality",
        "task": "对 {{artifact}} 做质检，输出 score 0~100",
        "output_variable": "review",
        "depends_on": ["execute"],
        "acceptance_text": "输出包含 score 与 feedback",
    },
)


@dataclass(frozen=True, slots=True)
class ComposeResult:
    """The YAML + run metadata produced by ``compose_initial_workflow``."""

    yaml_path: Path
    yaml_text: str
    step_count: int
    executor_role_path: str
    executor_agent_id: uuid.UUID | None


def _ao_role_path_for(agent: Agent, role_key: str) -> str:
    """Resolve an AO ``role_path`` for a Clawith Agent + ``role_key``.

    Lookup order (deterministic):

    1. ``SYSTEM_ROLE_PATH_HINTS[role_key]`` for the three power slots
       (scheduler / quality / delivery).
    2. ``agent.role_description`` if it looks like an AO path (contains a
       slash) — useful when the role was authored with a custom AO mapping.
    3. ``product/<role_key>`` when the role key looks human-friendly
       (``executor_0`` → ``product/executor-0``).
    4. ``_FALLBACK_ROLE_PATH`` to keep group provisioning unblocked.

    The function never raises: a missing role must not abort HR confirmation.
    """
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


def _select_executor_role(
    roles: list[dict],
    *,
    agent_ids: dict[str, uuid.UUID],
) -> tuple[str, uuid.UUID | None]:
    """Pick the first executor Agent from ``roles`` (skip the three power slots).

    Falls back to (fallback role path, None) when ``roles`` is empty or every
    role is already a power slot — keeps the YAML well-formed so downstream
    P1.4 can still attempt execution.
    """
    power_slots = set(agent_ids.keys())
    for role in roles:
        key = str(role.get("key") or "").strip()
        if not key or key in power_slots:
            continue
        return f"product/{key.replace('_', '-')}", None
    return _FALLBACK_ROLE_PATH, None


def _render_yaml_text(
    *,
    workflow_name: str,
    agents_dir: str,
    provider: str,
    model: str,
    concurrency: int,
    step_role_paths: dict[str, str],
) -> str:
    """Render the minimal AO YAML body Clawith consumes as the initial workflow.

    The structure mirrors ``AOClient.parse_workflow`` requirements: ``name``,
    ``agents_dir``, ``llm``, ``concurrency``, ``steps`` with mandatory ``id`` /
    ``role`` / ``task``. ``depends_on`` and ``output_variable`` are optional
    but emitted so P1.4 can render the run plan without re-deriving them.
    """
    steps_payload: list[dict] = []
    for template in _DEFAULT_DAG_TEMPLATE:
        step_id = template["step_id"]
        role_path = step_role_paths[step_id]
        steps_payload.append(
            {
                "id": step_id,
                "role": role_path,
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


def _resolve_step_role_paths(
    *,
    executor_role_path: str,
) -> dict[str, str]:
    """Materialize ``step_id -> role_path`` for the default DAG template."""
    return {
        template["step_id"]: (
            executor_role_path
            if template["step_id"] == "execute"
            else str(template["role_path"])
        )
        for template in _DEFAULT_DAG_TEMPLATE
    }


def _yaml_target_path(workflow_id: uuid.UUID) -> Path:
    """Return ``<AO_WORKFLOWS_DIR>/<workflow_id>.yaml``; create parent if missing."""
    base = Path(settings.AO_WORKFLOWS_DIR or "")
    if not base:
        base = Path(settings.AO_HOME_DIR or ".") / "workflows"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{workflow_id}.yaml"


async def compose_initial_workflow(
    db: AsyncSession,
    *,
    workflow: ProjectWorkflow,
    agent_ids: dict[str, uuid.UUID],
    roles: list[dict],
) -> tuple[Path, dict]:
    """Compose the initial AO workflow YAML + a metadata envelope for a workflow.

    Parameters
    ----------
    db:
        Async session, kept in the signature for forward compatibility (P1.4
        will need it for AOAgentTemplate lookups). The current implementation
        does not write to the DB; ``run_repository.create_run_row`` does.
    workflow:
        The freshly-provisioned ``ProjectWorkflow`` row whose ``id`` drives the
        YAML filename and whose ``name`` drives the AO ``name:`` field.
    agent_ids:
        Mapping ``"scheduler" | "quality" | "delivery" -> Agent.id`` produced
        by ``ensure_workflow_system_roles``. Missing keys still yield a valid
        YAML via ``_ao_role_path_for``'s fallbacks.
    roles:
        The HR proposal's ``roles`` list. The first non-power-slot role drives
        the ``execute`` step's ``role_path``; if none exist we fall back to
        ``_FALLBACK_ROLE_PATH`` so the run is still kickable.

    Returns
    -------
    (yaml_path, metadata)
        ``yaml_path`` is an absolute path to a YAML file written on disk.
        ``metadata`` contains ``yaml_text``, ``step_count`` and the executor
        metadata needed by ``run_repository.create_run_row``.
    """
    del db  # currently unused; kept so P1.4 can read AOAgentTemplate mappings.
    executor_role_path, _executor_agent_id = _select_executor_role(
        roles, agent_ids=agent_ids
    )
    step_role_paths = _resolve_step_role_paths(executor_role_path=executor_role_path)
    yaml_text = _render_yaml_text(
        workflow_name=workflow.name,
        agents_dir=str(settings.AO_AGENTS_DIR or "./agency-agents-zh"),
        provider=str(settings.AO_PROVIDER or "openai"),
        model=str(settings.AO_MODEL or "clawith-gateway"),
        concurrency=int(settings.AO_CONCURRENCY),
        step_role_paths=step_role_paths,
    )
    yaml_path = _yaml_target_path(workflow.id)
    yaml_path.write_text(yaml_text, encoding="utf-8")
    metadata = {
        "yaml_text": yaml_text,
        "step_count": len(_DEFAULT_DAG_TEMPLATE),
        "executor_role_path": executor_role_path,
        "executor_agent_id": None,
        "step_role_paths": step_role_paths,
    }
    logger.info(
        "[AOComposer] Composed workflow {} at {} (steps={}, executor_role={})",
        workflow.id,
        yaml_path,
        metadata["step_count"],
        executor_role_path,
    )
    return yaml_path, metadata