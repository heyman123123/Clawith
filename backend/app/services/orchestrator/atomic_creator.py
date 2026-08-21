"""AtomicCreator: LEGACY reference implementation.

Superseded by ``_create_project_from_draft()`` in
``backend/app/api/orchestrator.py``, which performs direct SQLAlchemy
inserts (faster, no service-injection ceremony, fully exercised by
end-to-end smoke tests).

AtomicCreator was the original spec-compliant path that delegates to
injected group_service / agent_service / okr_service / task_board_service /
command_intake_service. Operators can still wire those services and
re-enable this path if they want to route through the existing service
layer rather than direct DB inserts.

The deterministic-UUID helpers (``deterministic_uuid``) and the
draft-replay logic in ``_replay_result`` are still useful; the rest is
vestigial.

Idempotency key = draft_id. All downstream resources use SHA256-derived
UUIDs from (draft_id, suffix) so retries with the same draft_id produce
identical DB state without duplicate rows.
"""
from __future__ import annotations
import hashlib
import uuid
import logging
from typing import Any
from app.database import async_session
from app.models.draft import Draft as DraftModel

logger = logging.getLogger(__name__)


class AtomicCreatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AtomicCreator:
    """Materializes a Draft into real Group + Agents + OKR + Tasks + Chief Run.

    Dependencies are injected so tests can mock the side effects without
    needing the full Groups/Agents/OKR/TaskBoard services to be live.
    """

    def __init__(
        self,
        *,
        group_service=None,
        agent_service=None,
        okr_service=None,
        task_board_service=None,
        command_intake_service=None,
    ) -> None:
        self.group_service = group_service
        self.agent_service = agent_service
        self.okr_service = okr_service
        self.task_board_service = task_board_service
        self.command_intake_service = command_intake_service

    def deterministic_uuid(self, draft_id: uuid.UUID, suffix: str) -> uuid.UUID:
        """Stable UUID from (draft_id, suffix) for idempotent resource IDs."""
        h = hashlib.sha256(f"{draft_id}|{suffix}".encode()).hexdigest()
        return uuid.UUID(h[:32])

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        draft_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Materialize the Draft. Idempotent on draft_id."""
        async with async_session() as db:
            draft_row = await db.get(DraftModel, draft_id)
            if draft_row is None or draft_row.tenant_id != tenant_id:
                raise AtomicCreatorError("draft_not_found", f"Draft {draft_id} not found")
            if draft_row.status == "consumed":
                logger.info("AtomicCreator idempotent replay for draft %s", draft_id)
                return self._replay_result(draft_id, draft_row)
            payload = draft_row.draft_payload or {}
            if draft_row.status != "approved":
                raise AtomicCreatorError(
                    "draft_not_approved",
                    f"Draft must be 'approved' to materialize (current: {draft_row.status})",
                )

        # Deterministic IDs
        group_id = self.deterministic_uuid(draft_id, "group")
        chief_agent_id = self.deterministic_uuid(draft_id, "chief_agent")
        chief_run_id = self.deterministic_uuid(draft_id, "chief_run")

        # Step 1-4: delegate to injected services (real impls in W2.7 wiring)
        if self.group_service is not None:
            # group_service: callable(db, tenant_id, creator_participant_id, name, description, member_participant_ids) -> Group
            # We have to fetch/create the creator participant first.
            from app.services.participant_identity import get_or_create_user_participant
            from app.models.user import User
            creator_user = await db.get(User, draft_row.user_id)
            if creator_user is not None:
                creator_part = await get_or_create_user_participant(
                    db,
                    creator_user.id,
                    creator_user.display_name,
                    creator_user.avatar_url,
                )
                member_parts: list[uuid.UUID] = []
                for m in payload.get("members", []):
                    # for now we treat each member as the creator until agents are created below
                    pass
                await self.group_service(
                    db,
                    tenant_id=tenant_id,
                    creator_participant_id=creator_part.id,
                    name=payload.get("group", {}).get("name", "新项目"),
                    description=payload.get("group", {}).get("description", ""),
                    member_participant_ids=member_parts,
                )
        if self.agent_service is not None:
            for m in payload.get("members", []):
                await self.agent_service.create(
                    tenant_id=tenant_id,
                    name=m.get("name", "Agent"),
                    role=m.get("role", "agent"),
                    system_prompt=m.get("system_prompt", ""),
                    template_id=m.get("template_id"),
                    is_new_template=m.get("is_new_template", False),
                    group_id=group_id if m.get("role") != "chief-of-staff" else None,
                )
            await self.agent_service.create(
                tenant_id=tenant_id,
                agent_id=chief_agent_id,
                name="Chief of Staff",
                role="chief-of-staff",
                system_prompt="You are the Chief of Staff coordinating this group.",
                group_id=group_id,
                is_chief=True,
            )
        if self.okr_service is not None and payload.get("okr"):
            await self.okr_service.create_objective(
                tenant_id=tenant_id,
                title=payload["okr"].get("objective_title", ""),
                description=payload["okr"].get("objective_description", ""),
                owner_group_id=group_id,
            )

        task_card_ids: list[uuid.UUID] = []
        if self.task_board_service is not None:
            task_card_ids = await self.task_board_service.create_initial_cards(
                tenant_id=tenant_id,
                group_id=group_id,
                tasks=payload.get("tasks", []),
            )

        # Step 5: Dispatch start_chief_run via RuntimeCommandIntake (C1)
        if self.command_intake_service is not None:
            await self.command_intake_service.enqueue_start_chief_run(
                tenant_id=tenant_id,
                chief_run_id=chief_run_id,
                group_id=group_id,
                chief_agent_id=chief_agent_id,
                runtime_thread_id=f"orchestrator:{group_id}",
            )

        # Step 6: Mark draft consumed (idempotency anchor)
        async with async_session() as db:
            draft_row = await db.get(DraftModel, draft_id)
            if draft_row:
                draft_row.status = "consumed"
                draft_row.error_detail = {
                    "group_id": str(group_id),
                    "chief_run_id": str(chief_run_id),
                    "task_card_ids": [str(t) for t in task_card_ids],
                }
                await db.commit()

        return {
            "draft_id": draft_id,
            "group_id": group_id,
            "chief_agent_id": chief_agent_id,
            "chief_run_id": chief_run_id,
            "task_card_ids": task_card_ids,
            "okr_objective_id": None,
        }

    def _replay_result(
        self, draft_id: uuid.UUID, draft_row: DraftModel
    ) -> dict[str, Any]:
        """Return previously-computed result for an already-consumed draft."""
        detail = draft_row.error_detail or {}
        return {
            "draft_id": draft_id,
            "group_id": uuid.UUID(detail["group_id"]) if "group_id" in detail else self.deterministic_uuid(draft_id, "group"),
            "chief_agent_id": self.deterministic_uuid(draft_id, "chief_agent"),
            "chief_run_id": uuid.UUID(detail["chief_run_id"]) if "chief_run_id" in detail else self.deterministic_uuid(draft_id, "chief_run"),
            "task_card_ids": [uuid.UUID(t) for t in detail.get("task_card_ids", [])],
            "okr_objective_id": None,
        }
