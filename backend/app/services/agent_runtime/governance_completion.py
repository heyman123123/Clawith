"""Process governance JSON outputs from terminal group chat Runs."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.board_escalation import BoardEscalation
from app.models.chat_session import ChatSession
from app.models.project import ProjectWorkflow
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.board_escalation_service import (
    extract_board_resolution,
    extract_escalation_payload,
    process_shareholder_escalation_output,
)
from app.services.decision_record_service import (
    extract_decision_summary,
    process_decision_group_agent_output,
)


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


def _may_contain_decision_output(text: str) -> bool:
    return (
        extract_escalation_payload(text) is not None
        or extract_decision_summary(text) is not None
    )


async def _primary_group_session(
    db,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
) -> ChatSession | None:
    return await db.scalar(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.group_id == group_id,
            ChatSession.session_type == "group",
            ChatSession.is_primary.is_(True),
            ChatSession.deleted_at.is_(None),
        )
        .limit(1)
    )


class GovernanceRuntimeCompletionHandler:
    """Finalize decision-group and shareholder governance outputs at terminal."""

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

                if extract_board_resolution(text) is not None:
                    escalation = await db.scalar(
                        select(BoardEscalation.id).where(
                            BoardEscalation.shareholder_session_id == session.id,
                            BoardEscalation.status == "open",
                        )
                    )
                    if escalation is not None:
                        await process_shareholder_escalation_output(
                            db,
                            shareholder_session_id=session.id,
                            text=text,
                        )
                        return

                if not _may_contain_decision_output(text):
                    return

                workflow = await db.scalar(
                    select(ProjectWorkflow).where(
                        ProjectWorkflow.tenant_id == run.tenant_id,
                        ProjectWorkflow.decision_group_id == session.group_id,
                    )
                )
                if workflow is None or workflow.group_id is None:
                    return

                project_session = await _primary_group_session(
                    db,
                    tenant_id=run.tenant_id,
                    group_id=workflow.group_id,
                )
                if project_session is None:
                    return

                await process_decision_group_agent_output(
                    db,
                    tenant_id=run.tenant_id,
                    workflow=workflow,
                    decision_session_id=session.id,
                    project_session_id=project_session.id,
                    text=text,
                    participants=[],
                )


__all__ = ["GovernanceRuntimeCompletionHandler"]
