"""Board escalation: decision group → shareholder board → decision group."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.board_escalation import BoardEscalation
from app.models.chat_session import ChatSession
from app.models.governance import GovernanceRolePool
from app.models.group import Group
from app.models.participant import Participant
from app.models.project import ProjectWorkflow
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_chat_service
from app.services.participant_identity import get_or_create_agent_participant, get_or_create_user_participant

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_ESCALATION_KEY = re.compile(r'(\{.*?"escalation_needed"\s*:\s*true.*?\})', re.S | re.I)
_BOARD_RESOLUTION_KEY = re.compile(r'"board_resolution"\s*:\s*(\{.*?\})', re.S)

DECISION_ESCALATION_PROMPT_SNIPPET = """
## Escalation Output Protocol
When a governance review cannot reach consensus, emit a fenced JSON block:

```json
{
  "escalation_needed": true,
  "unresolved_points": ["..."],
  "options": [{"id": "A", "summary": "..."}, {"id": "B", "summary": "..."}],
  "recommended_option_id": null
}
```

Do not emit `decision_summary` task mutations in the same message when escalating.
""".strip()

BOARD_SECRETARY_RESOLUTION_PROMPT_SNIPPET = """
## Board Resolution Protocol
After shareholder deliberation, Board Secretary MUST emit:

```json
{
  "board_resolution": {
    "summary": "...",
    "chosen_option_id": "A",
    "constraints": ["..."],
    "authority_granted": "..."
  }
}
```

Humans confirm by @Board Secretary. Resolutions are written back to the project decision group only.
""".strip()


def is_escalation_payload(payload: dict[str, Any]) -> bool:
    return bool(payload.get("escalation_needed"))


def parse_escalation_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an escalation payload."""
    if not isinstance(raw, dict):
        raise ValueError("Escalation payload must be an object")
    if not raw.get("escalation_needed"):
        raise ValueError("escalation_needed must be true")
    unresolved = raw.get("unresolved_points")
    if not isinstance(unresolved, list) or not unresolved:
        raise ValueError("unresolved_points must be a non-empty list")
    options = raw.get("options") or []
    if not isinstance(options, list):
        raise ValueError("options must be a list")
    normalized_options: list[dict[str, str]] = []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            raise ValueError("each option must be an object")
        option_id = str(option.get("id") or f"option_{index + 1}").strip()
        summary = str(option.get("summary") or "").strip()
        if not option_id or not summary:
            raise ValueError("each option requires id and summary")
        normalized_options.append({"id": option_id, "summary": summary})
    recommended = raw.get("recommended_option_id")
    return {
        "escalation_needed": True,
        "unresolved_points": [str(point).strip() for point in unresolved if str(point).strip()],
        "options": normalized_options,
        "recommended_option_id": str(recommended).strip() if recommended else None,
    }


def _extract_json_object(text: str, *, extra_pattern: re.Pattern[str] | None = None) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    patterns = [_JSON_FENCE]
    if extra_pattern is not None:
        patterns.append(extra_pattern)
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def extract_escalation_payload(text: str) -> dict[str, Any] | None:
    """Extract an escalation payload from assistant text."""
    payload = _extract_json_object(text, extra_pattern=_ESCALATION_KEY)
    if payload is None:
        return None
    if "escalation_needed" in payload and isinstance(payload.get("escalation_payload"), dict):
        payload = payload["escalation_payload"]
    if not is_escalation_payload(payload):
        return None
    return parse_escalation_payload(payload)


def parse_board_resolution(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Board resolution must be an object")
    resolution = raw.get("board_resolution")
    if not isinstance(resolution, dict):
        raise ValueError("board_resolution must be an object")
    summary = str(resolution.get("summary") or "").strip()
    if not summary:
        raise ValueError("board_resolution.summary is required")
    chosen_option_id = resolution.get("chosen_option_id")
    constraints = resolution.get("constraints") or []
    if not isinstance(constraints, list):
        raise ValueError("board_resolution.constraints must be a list")
    return {
        "board_resolution": {
            "summary": summary,
            "chosen_option_id": str(chosen_option_id).strip() if chosen_option_id else None,
            "constraints": [str(item).strip() for item in constraints if str(item).strip()],
            "authority_granted": str(resolution.get("authority_granted") or "").strip(),
        }
    }


def extract_board_resolution(text: str) -> dict[str, Any] | None:
    payload = _extract_json_object(text, extra_pattern=_BOARD_RESOLUTION_KEY)
    if payload is None:
        return None
    return parse_board_resolution(payload)


def build_escalation_case_brief(
    *,
    workflow_name: str,
    escalation_id: uuid.UUID,
    payload: dict[str, Any],
) -> str:
    points = payload.get("unresolved_points") or []
    options = payload.get("options") or []
    lines = [
        f"<!--board_escalation:{escalation_id}-->",
        f"📌 **决策升级案卷 · {workflow_name}**",
        "",
        "**未决争议点**",
        *[f"- {point}" for point in points],
        "",
        "**备选方案**",
    ]
    for option in options:
        lines.append(f"- [{option.get('id')}] {option.get('summary')}")
    recommended = payload.get("recommended_option_id")
    if recommended:
        lines.extend(["", f"决策群倾向选项：{recommended}"])
    lines.extend(
        [
            "",
            "请 @Board Secretary 汇总股东意见并输出 `board_resolution` JSON。",
        ]
    )
    return "\n".join(lines)


def build_board_resolution_sync_content(
    *,
    escalation_id: uuid.UUID,
    resolution: dict[str, Any],
) -> str:
    body = resolution.get("board_resolution") or resolution
    summary = body.get("summary") or ""
    chosen = body.get("chosen_option_id")
    constraints = body.get("constraints") or []
    authority = body.get("authority_granted") or ""
    lines = [
        f"<!--board_resolution:{escalation_id}-->",
        "🏛️ **股东会决议回写**",
        "",
        f"**决议摘要**：{summary}",
    ]
    if chosen:
        lines.append(f"**选定方案**：{chosen}")
    if constraints:
        lines.extend(["", "**约束**", *[f"- {item}" for item in constraints]])
    if authority:
        lines.extend(["", f"**授权范围**：{authority}"])
    lines.extend(
        [
            "",
            "请决策群 Agent 据此补全 `decision_summary` 并下发执行群；股东路径不得直接改任务。",
        ]
    )
    return "\n".join(lines)


async def _board_secretary_participant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> Participant:
    pool_row = await db.scalar(
        select(GovernanceRolePool).where(
            GovernanceRolePool.tenant_id == tenant_id,
            GovernanceRolePool.role_key == "board_secretary",
        )
    )
    if pool_row is None:
        raise RuntimeError("Board Secretary role pool is missing")
    agent = await db.get(Agent, pool_row.agent_id)
    if agent is None or agent.deleted_at is not None:
        raise RuntimeError("Board Secretary agent is missing")
    return await get_or_create_agent_participant(
        db,
        agent.id,
        display_name=agent.name,
        avatar_url=agent.avatar_url,
    )


async def _default_shareholder_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
) -> ChatSession:
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.group_id == group_id,
            ChatSession.session_type == "group",
            ChatSession.is_primary.is_(True),
            ChatSession.deleted_at.is_(None),
        ).limit(1)
    )
    if session is None:
        raise RuntimeError("Shareholder group is missing a primary session")
    return session


async def _system_sender_participant(
    db: AsyncSession,
    *,
    group: Group,
    tenant_id: uuid.UUID,
) -> Participant:
    if group.owner_agent_id is not None:
        owner = await db.get(Agent, group.owner_agent_id)
        if owner is not None and owner.deleted_at is None:
            return await get_or_create_agent_participant(
                db,
                owner.id,
                display_name=owner.name,
                avatar_url=owner.avatar_url,
            )
    return await _board_secretary_participant(db, tenant_id=tenant_id)


async def open_board_escalation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_group_id: uuid.UUID,
    decision_session_id: uuid.UUID,
    workflow_id: uuid.UUID | None,
    payload: dict[str, Any],
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None = None,
) -> BoardEscalation:
    """Open one shareholder escalation session; idempotent per open decision session."""
    normalized = parse_escalation_payload(payload)
    existing = await db.scalar(
        select(BoardEscalation).where(
            BoardEscalation.decision_session_id == decision_session_id,
            BoardEscalation.status == "open",
        )
    )
    if existing is not None:
        return existing

    if model_id is None:
        tenant = await db.get(Tenant, tenant_id)
        model_id = tenant.default_model_id if tenant is not None else None

    from app.services.shareholder_group_seeder import ensure_shareholder_group

    shareholder_group = await ensure_shareholder_group(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
    )
    primary_session = await _default_shareholder_session(
        db,
        tenant_id=tenant_id,
        group_id=shareholder_group.id,
    )
    secretary_participant = await _board_secretary_participant(db, tenant_id=tenant_id)
    creator = await db.get(User, creator_id)
    creator_display = creator.display_name if creator is not None else "Tenant Admin"
    creator_avatar = creator.avatar_url if creator is not None else None
    creator_participant = await get_or_create_user_participant(
        db,
        creator_id,
        creator_display,
        creator_avatar,
    )
    shareholder_session = await group_chat_service.create_group_session(
        db,
        tenant_id=tenant_id,
        group_id=shareholder_group.id,
        actor_participant_id=creator_participant.id,
        title="决策升级",
        parent_session_id=primary_session.id,
    )

    escalation_id = uuid.uuid4()
    workflow_name = "项目决策"
    if workflow_id is not None:
        workflow = await db.get(ProjectWorkflow, workflow_id)
        if workflow is not None and workflow.name:
            workflow_name = workflow.name

    case_brief = build_escalation_case_brief(
        workflow_name=workflow_name,
        escalation_id=escalation_id,
        payload=normalized,
    )
    from app.services.group_message_service import enqueue_group_message

    await enqueue_group_message(
        db,
        tenant_id=tenant_id,
        group_id=shareholder_group.id,
        session_id=shareholder_session.id,
        sender_participant_id=secretary_participant.id,
        content=case_brief,
        mention_participant_ids=[secretary_participant.id],
        message_id=uuid.uuid5(decision_session_id, "board-escalation-open"),
        project_task_dispatch=False,
    )

    escalation = BoardEscalation(
        id=escalation_id,
        tenant_id=tenant_id,
        decision_group_id=decision_group_id,
        decision_session_id=decision_session_id,
        shareholder_group_id=shareholder_group.id,
        shareholder_session_id=shareholder_session.id,
        workflow_id=workflow_id,
        status="open",
        escalation_payload=normalized,
    )
    db.add(escalation)
    await db.flush()
    return escalation


async def apply_board_resolution(
    db: AsyncSession,
    *,
    escalation_id: uuid.UUID,
    resolution: dict[str, Any],
) -> BoardEscalation:
    """Write a board resolution back to the decision group without mutating execution tasks."""
    escalation = await db.get(BoardEscalation, escalation_id)
    if escalation is None:
        raise ValueError("Board escalation not found")
    if escalation.status != "open":
        raise ValueError("Board escalation is not open")

    normalized = parse_board_resolution(resolution)
    decision_group = await db.get(Group, escalation.decision_group_id)
    if decision_group is None or decision_group.deleted_at is not None:
        raise RuntimeError("Decision group is missing")

    sender = await _system_sender_participant(
        db,
        group=decision_group,
        tenant_id=escalation.tenant_id,
    )
    content = build_board_resolution_sync_content(
        escalation_id=escalation.id,
        resolution=normalized,
    )
    from app.services.group_message_service import enqueue_group_message

    await enqueue_group_message(
        db,
        tenant_id=escalation.tenant_id,
        group_id=escalation.decision_group_id,
        session_id=escalation.decision_session_id,
        sender_participant_id=sender.id,
        content=content,
        mention_participant_ids=[sender.id],
        message_id=uuid.uuid5(escalation.id, "board-resolution-sync"),
        project_task_dispatch=False,
    )

    escalation.board_resolution = normalized
    escalation.status = "resolved"
    escalation.resolved_at = datetime.now(UTC)
    await db.flush()
    return escalation


async def process_shareholder_escalation_output(
    db: AsyncSession,
    *,
    shareholder_session_id: uuid.UUID,
    text: str,
) -> BoardEscalation | None:
    """Parse board_resolution from shareholder session output and apply it."""
    resolution = extract_board_resolution(text)
    if resolution is None:
        return None
    escalation = await db.scalar(
        select(BoardEscalation).where(
            BoardEscalation.shareholder_session_id == shareholder_session_id,
            BoardEscalation.status == "open",
        )
    )
    if escalation is None:
        return None
    return await apply_board_resolution(db, escalation_id=escalation.id, resolution=resolution)
