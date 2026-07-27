"""Persist HR review proposals when HR agents finish a group chat Run."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.chat_session import ChatSession
from app.models.group import Group
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.hr_review_board_seeder import HR_REVIEW_BOARD_GROUP_TYPE
from app.services.hr_review_session_service import process_hr_group_agent_output


def _terminal_answer(checkpoint: CheckpointObservation) -> str | None:
    lifecycle = checkpoint.state["lifecycle"]
    if lifecycle.get("status") != "completed":
        return None
    final_answer = lifecycle.get("final_answer")
    if isinstance(final_answer, str) and final_answer.strip():
        return final_answer.strip()
    raw_request = lifecycle.get("delivery_request")
    if isinstance(raw_request, dict):
        content = raw_request.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


class HrRuntimeCompletionHandler:
    """Attach team-building proposals when HR board agents emit proposal JSON."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        if run.source_type != "chat" or run.system_role == "group_planning":
            return
        text = _terminal_answer(checkpoint)
        if not text:
            return
        try:
            session_id = uuid.UUID(run.session_id or "")
        except ValueError:
            return

        async with self._session_factory() as db:
            async with db.begin():
                session = await db.scalar(
                    select(ChatSession).where(
                        ChatSession.tenant_id == run.tenant_id,
                        ChatSession.id == session_id,
                        ChatSession.deleted_at.is_(None),
                    )
                )
                if session is None or session.group_id is None:
                    return
                group = await db.get(Group, session.group_id)
                if group is None or group.deleted_at is not None:
                    return
                if group.group_type != HR_REVIEW_BOARD_GROUP_TYPE:
                    return
                await process_hr_group_agent_output(
                    db,
                    tenant_id=run.tenant_id,
                    chat_session_id=session_id,
                    text=text,
                )


__all__ = ["HrRuntimeCompletionHandler"]
