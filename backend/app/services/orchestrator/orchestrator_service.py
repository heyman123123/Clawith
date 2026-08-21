"""OrchestratorService: end-to-end propose + (optionally) create pipeline.

Composes IntentAnalyzer -> AgentSelector -> GroupPlanner -> DraftRenderer
into a single service object. AtomicCreator is separate (T2.6) because
materialization is a different lifecycle stage from proposal.
"""
from __future__ import annotations
import uuid
from app.database import async_session
from app.models.draft import Draft as DraftModel
from app.services.orchestrator.intent_analyzer import IntentAnalyzer
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.group_planner import GroupPlanner
from app.services.orchestrator.draft_schemas import (
    Draft,
    DraftSummary,
    AgentProposal,
    GroupProposal,
    OKRProposal,
    TaskProposal,
)


class OrchestratorService:
    def __init__(
        self,
        *,
        intent_analyzer: IntentAnalyzer,
        agent_selector: AgentSelector,
        group_planner: GroupPlanner,
    ) -> None:
        self.intent_analyzer = intent_analyzer
        self.agent_selector = agent_selector
        self.group_planner = group_planner

    async def propose(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        user_message: str,
    ) -> Draft:
        summary = await self.intent_analyzer.analyze(
            user_message, tenant_id=tenant_id, user_id=user_id
        )
        members = await self.agent_selector.select(
            summary=summary, tenant_id=tenant_id
        )
        group, okr, tasks = await self.group_planner.plan(
            summary=summary, members=members, user_message=user_message
        )
        draft_id = uuid.uuid4()
        async with async_session() as db:
            draft_row = DraftModel(
                id=draft_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_message=user_message,
                intent_summary=summary.model_dump(),
                draft_payload={
                    "group": group.model_dump(),
                    "members": [m.model_dump(mode="json") for m in members],
                    "okr": okr.model_dump(mode="json") if okr else None,
                    "tasks": [t.model_dump() for t in tasks],
                },
                template_visibility="user_private",
                status="pending",
            )
            db.add(draft_row)
            await db.commit()
            await db.refresh(draft_row)

        return Draft(
            id=draft_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_message=user_message,
            summary=summary,
            group=group,
            members=members,
            okr=okr,
            tasks=tasks,
            template_visibility="user_private",
            status="pending",
            created_at=draft_row.created_at,
            updated_at=draft_row.updated_at,
        )

    async def approve(
        self, *, tenant_id: uuid.UUID, draft_id: uuid.UUID
    ) -> Draft:
        """Mark a Draft as approved so AtomicCreator can materialize it."""
        async with async_session() as db:
            draft_row = await db.get(DraftModel, draft_id)
            if draft_row is None or draft_row.tenant_id != tenant_id:
                raise ValueError(f"Draft {draft_id} not found")
            if draft_row.status not in ("pending",):
                raise ValueError(f"Draft status is {draft_row.status}; cannot approve")
            draft_row.status = "approved"
            await db.commit()
            await db.refresh(draft_row)
        return await self.get(tenant_id=tenant_id, draft_id=draft_id)

    async def get(self, *, tenant_id: uuid.UUID, draft_id: uuid.UUID) -> Draft:
        async with async_session() as db:
            draft_row = await db.get(DraftModel, draft_id)
            if draft_row is None or draft_row.tenant_id != tenant_id:
                raise ValueError(f"Draft {draft_id} not found")
            return _hydrate_draft(draft_row)

    async def list_for_user(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Draft]:
        from sqlalchemy import select
        async with async_session() as db:
            result = await db.execute(
                select(DraftModel)
                .where(
                    DraftModel.tenant_id == tenant_id,
                    DraftModel.user_id == user_id,
                )
                .order_by(DraftModel.created_at.desc())
            )
            return [_hydrate_draft(r) for r in result.scalars().all()]


def _hydrate_draft(row: DraftModel) -> Draft:
    payload = row.draft_payload or {}
    return Draft(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        user_message=row.user_message,
        summary=DraftSummary(**(row.intent_summary or {"intent_category": "other", "scope_summary": "", "key_constraints": []})),
        group=GroupProposal(**payload.get("group", {"name": "", "description": ""})),
        members=[AgentProposal(**m) for m in payload.get("members", [])],
        okr=OKRProposal(**payload["okr"]) if payload.get("okr") else None,
        tasks=[TaskProposal(**t) for t in payload.get("tasks", [])],
        template_visibility=row.template_visibility,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
