"""FastAPI router for the Task Board.

NOT auto-mounted in main.py (operator wires explicitly).
Endpoints:
  POST /api/task-board/cards              — create a card
  GET  /api/task-board/cards?group_id=X  — list cards on a group board
  PATCH /api/task-board/cards/{id}/move  — move a card (with optimistic lock)
  PATCH /api/task-board/cards/{id}/assign — assign a card
  POST /api/task-board/cards/{id}/done   — mark done
"""
from __future__ import annotations
import uuid
from fastapi import APIRouter, HTTPException, status, Query
from app.services.task_board.service import TaskBoardService, TaskBoardError
from app.services.task_board.column_defs import TaskColumn

router = APIRouter(prefix="/api/task-board", tags=["task-board"])


def _service() -> TaskBoardService:
    return TaskBoardService()


@router.post("/cards", status_code=status.HTTP_201_CREATED)
async def create_card(payload: dict):
    tenant_id = payload.get("tenant_id")
    actor_id = payload.get("actor_id")
    actor_type = payload.get("actor_type", "user")
    title = payload.get("title")
    if not (tenant_id and actor_id and title):
        raise HTTPException(status_code=400, detail="tenant_id, actor_id, title required")
    try:
        card = await _service().create_card(
            tenant_id=uuid.UUID(tenant_id),
            actor_id=uuid.UUID(actor_id),
            actor_type=actor_type,
            title=title,
            group_id=uuid.UUID(payload["group_id"]) if payload.get("group_id") else None,
            assignee_agent_id=uuid.UUID(payload["assignee_agent_id"]) if payload.get("assignee_agent_id") else None,
            okr_key_result_id=uuid.UUID(payload["okr_key_result_id"]) if payload.get("okr_key_result_id") else None,
            description=payload.get("description"),
            priority=payload.get("priority"),
            depends_on_card_ids=[uuid.UUID(x) for x in payload["depends_on_card_ids"]] if payload.get("depends_on_card_ids") else None,
        )
        return {"id": str(card.id), "version": card.version, "column": card.column}
    except TaskBoardError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.get("/cards")
async def list_cards(
    tenant_id: str = Query(...),
    group_id: str | None = Query(None),
    agent_id: str | None = Query(None),
):
    from app.services.task_board.dao import TaskCardDAO
    from app.database import async_session
    async with async_session() as db:
        dao = TaskCardDAO(db)
        if group_id:
            cards = await dao.list_cards_by_group(uuid.UUID(group_id), tenant_id=uuid.UUID(tenant_id))
        elif agent_id:
            cards = await dao.list_cards_by_assignee(uuid.UUID(agent_id), tenant_id=uuid.UUID(tenant_id))
        else:
            raise HTTPException(status_code=400, detail="group_id or agent_id required")
        return [
            {
                "id": str(c.id),
                "title": c.title,
                "column": c.column,
                "position": c.position,
                "version": c.version,
                "assignee_agent_id": str(c.assignee_agent_id) if c.assignee_agent_id else None,
                "artifact_paths": list(c.artifact_paths or []),
            }
            for c in cards
        ]


@router.patch("/cards/{card_id}/move")
async def move_card(card_id: uuid.UUID, payload: dict):
    tenant_id = payload.get("tenant_id")
    actor_id = payload.get("actor_id")
    to_column = payload.get("to_column")
    expected_version = payload.get("expected_version")
    if not (tenant_id and actor_id and to_column is not None and expected_version is not None):
        raise HTTPException(status_code=400, detail="tenant_id, actor_id, to_column, expected_version required")
    try:
        card = await _service().move_card(
            tenant_id=uuid.UUID(tenant_id),
            card_id=card_id,
            to_column=TaskColumn(to_column),
            actor_id=uuid.UUID(actor_id),
            actor_type=payload.get("actor_type", "user"),
            expected_version=expected_version,
        )
        return {"id": str(card.id), "column": card.column, "version": card.version}
    except TaskBoardError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.patch("/cards/{card_id}/assign")
async def assign_card(card_id: uuid.UUID, payload: dict):
    tenant_id = payload.get("tenant_id")
    actor_id = payload.get("actor_id")
    assignee_agent_id = payload.get("assignee_agent_id")
    expected_version = payload.get("expected_version")
    if not (tenant_id and actor_id and assignee_agent_id and expected_version is not None):
        raise HTTPException(status_code=400, detail="missing required fields")
    try:
        card = await _service().assign_card(
            tenant_id=uuid.UUID(tenant_id),
            card_id=card_id,
            assignee_agent_id=uuid.UUID(assignee_agent_id),
            actor_id=uuid.UUID(actor_id),
            actor_type=payload.get("actor_type", "user"),
            expected_version=expected_version,
        )
        return {"id": str(card.id), "assignee_agent_id": str(card.assignee_agent_id), "version": card.version}
    except TaskBoardError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})


@router.post("/cards/{card_id}/done")
async def mark_done(card_id: uuid.UUID, payload: dict):
    tenant_id = payload.get("tenant_id")
    actor_id = payload.get("actor_id")
    expected_version = payload.get("expected_version")
    if not (tenant_id and actor_id and expected_version is not None):
        raise HTTPException(status_code=400, detail="missing required fields")
    try:
        card = await _service().mark_done(
            tenant_id=uuid.UUID(tenant_id),
            card_id=card_id,
            actor_id=uuid.UUID(actor_id),
            actor_type=payload.get("actor_type", "chief"),
            expected_version=expected_version,
            artifact_paths=payload.get("artifact_paths"),
        )
        return {"id": str(card.id), "column": card.column, "version": card.version, "artifact_paths": list(card.artifact_paths or [])}
    except TaskBoardError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})
