"""Delivery approval + cross-cutting human review API (P3 / P7).

Endpoints:
* ``POST /api/delivery-review/{workflow_id}/round`` — submit a 2D score (quality + coverage)
* ``GET  /api/delivery-review/{workflow_id}`` — list rounds for a workflow
* ``GET  /api/human-reviews`` — list pending human review cards (high-risk skills, QC anomalies, decisions, approvals)
* ``POST /api/human-reviews/{id}/resolve`` — approve / reject a review card

需求 §3.4 + §4.11 + §8.3 + §8.4.
"""

# ruff: noqa: B008  -- FastAPI Depends() in signature is the documented idiom.

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.delivery_review import WorkflowDeliveryApproval, WorkflowHumanReview
from app.models.user import User
from app.services.delivery_scoring import (
    DEFAULT_PASS_THRESHOLD,
    MAX_ROUNDS,
    ScoringResult,
    attempt_label,
    build_review_payload,
    compute_final_score,
    new_round_no,
)

router = APIRouter(tags=["delivery-review"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DeliveryRoundIn(BaseModel):
    """Two-dimension scoring rubric — see :mod:`app.services.delivery_scoring`."""

    model_config = ConfigDict(extra="forbid")

    quality_score: float = Field(ge=0, le=100)
    coverage_score: float = Field(ge=0, le=100)
    pass_threshold: int | None = Field(default=None, ge=0, le=100)
    coverage_notes: str | None = None
    quality_notes: str | None = None
    rectification_items: list[dict] = Field(default_factory=list)


class DeliveryRoundOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    round_no: int
    quality_score: float | None
    coverage_score: float | None
    final_score: float | None
    decision: str
    pass_threshold: int
    passed: bool
    exhausted: bool
    created_at: datetime
    decided_at: datetime | None


class HumanReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "high_risk_skill",
        "qc_anomaly_rectification",
        "shareholder_decision",
        "approval_card",
        "decision_card",
        "rectification",
    ]
    payload: dict = Field(default_factory=dict)
    workflow_id: uuid.UUID | None = None
    skill_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None


class HumanReviewResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected", "withdrawn", "auto_resolved"]
    notes: str | None = None


class HumanReviewOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID | None
    skill_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    kind: str
    status: str
    payload: dict
    decision_notes: str | None
    created_at: datetime
    resolved_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_id(user: User) -> uuid.UUID:
    tenant = getattr(user, "tenant_id", None)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no tenant association",
        )
    return tenant


def _serialize_approval(row: WorkflowDeliveryApproval, verdict: ScoringResult | None) -> dict:
    return {
        "id": str(row.id),
        "workflow_id": str(row.workflow_id),
        "round_no": row.round_no,
        "quality_score": row.quality_score,
        "coverage_score": row.coverage_score,
        "final_score": row.final_score,
        "decision": row.decision,
        "pass_threshold": verdict.pass_threshold if verdict else DEFAULT_PASS_THRESHOLD,
        "passed": bool(verdict and verdict.passed),
        "exhausted": bool(verdict and verdict.exhausted),
        "created_at": row.created_at,
        "decided_at": row.decided_at,
    }


def _serialize_human(row: WorkflowHumanReview) -> dict:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "workflow_id": str(row.workflow_id) if row.workflow_id else None,
        "skill_id": str(row.skill_id) if row.skill_id else None,
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "kind": row.kind,
        "status": row.status,
        "payload": row.payload or {},
        "decision_notes": row.decision_notes,
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/delivery-review/{workflow_id}/round",
    response_model=DeliveryRoundOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_delivery_round(
    workflow_id: uuid.UUID,
    body: DeliveryRoundIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """P3 — submit a two-dimension delivery review (quality 60% + coverage 40%)."""
    tenant_id = _tenant_id(current_user)

    existing_rows = (
        await db.execute(
            select(WorkflowDeliveryApproval)
            .where(
                WorkflowDeliveryApproval.tenant_id == tenant_id,
                WorkflowDeliveryApproval.workflow_id == workflow_id,
            )
            .order_by(WorkflowDeliveryApproval.round_no.desc())
        )
    ).scalars().all()
    next_round = new_round_no(existing_rows[0].round_no if existing_rows else None)
    if next_round > MAX_ROUNDS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Maximum of {MAX_ROUNDS} delivery rounds exceeded; escalate to shareholders.",
        )

    pass_threshold = body.pass_threshold or DEFAULT_PASS_THRESHOLD
    verdict = compute_final_score(
        quality=body.quality_score,
        coverage=body.coverage_score,
        pass_threshold=pass_threshold,
        round_no=next_round,
    )

    decision = "approved" if verdict.passed else "rejected"
    row = WorkflowDeliveryApproval(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        round_no=next_round,
        quality_score=verdict.quality,
        coverage_score=verdict.coverage,
        final_score=float(verdict.final_score),
        decision=decision,
        delivery_manager_id=current_user.id,
        coverage_notes=body.coverage_notes,
        quality_notes=body.quality_notes,
        rectification_items=body.rectification_items,
        decided_at=datetime.now(UTC),
    )
    db.add(row)

    if not verdict.passed and verdict.exhausted:
        db.add(
            WorkflowHumanReview(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                kind="shareholder_decision",
                status="open",
                payload=build_review_payload(
                    workflow_id=workflow_id,
                    kind="shareholder_decision",
                    payload={
                        "round_no": next_round,
                        "final_score": verdict.final_score,
                        "pass_threshold": verdict.pass_threshold,
                        "label": attempt_label(next_round),
                    },
                )["payload"],
                requester_user_id=current_user.id,
            )
        )

    await db.commit()
    await db.refresh(row)
    return _serialize_approval(row, verdict)


@router.get(
    "/delivery-review/{workflow_id}",
    response_model=list[DeliveryRoundOut],
)
async def list_delivery_rounds(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """P3 — paginate the historical delivery rounds for a workflow."""
    tenant_id = _tenant_id(current_user)
    rows = (
        await db.execute(
            select(WorkflowDeliveryApproval)
            .where(
                WorkflowDeliveryApproval.tenant_id == tenant_id,
                WorkflowDeliveryApproval.workflow_id == workflow_id,
            )
            .order_by(WorkflowDeliveryApproval.round_no.asc())
        )
    ).scalars().all()
    out: list[dict] = []
    for r in rows:
        verdict = compute_final_score(
            quality=r.quality_score or 0.0,
            coverage=r.coverage_score or 0.0,
            round_no=r.round_no,
        ) if r.quality_score is not None and r.coverage_score is not None else None
        out.append(_serialize_approval(r, verdict))
    return out


@router.post(
    "/human-reviews",
    response_model=HumanReviewOut,
    status_code=status.HTTP_201_CREATED,
)
async def open_human_review(
    body: HumanReviewIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Open a human review card (审批卡 / 决策卡 / 高危技能审核 / 质检异常人工复核)."""
    tenant_id = _tenant_id(current_user)
    row = WorkflowHumanReview(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_id=body.workflow_id,
        skill_id=body.skill_id,
        agent_id=body.agent_id,
        kind=body.kind,
        status="open",
        payload=body.payload,
        requester_user_id=current_user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize_human(row)


@router.get(
    "/human-reviews",
    response_model=list[HumanReviewOut],
)
async def list_human_reviews(
    status_filter: str | None = Query(default="open", alias="status"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List human review cards filtered by status / kind."""
    tenant_id = _tenant_id(current_user)
    stmt = select(WorkflowHumanReview).where(WorkflowHumanReview.tenant_id == tenant_id)
    if status_filter:
        stmt = stmt.where(WorkflowHumanReview.status == status_filter)
    if kind:
        stmt = stmt.where(WorkflowHumanReview.kind == kind)
    stmt = stmt.order_by(WorkflowHumanReview.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_human(r) for r in rows]


@router.post(
    "/human-reviews/{review_id}/resolve",
    response_model=HumanReviewOut,
)
async def resolve_human_review(
    review_id: uuid.UUID,
    body: HumanReviewResolveIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve / reject / withdraw a human review card."""
    tenant_id = _tenant_id(current_user)
    row = (
        await db.execute(
            select(WorkflowHumanReview).where(
                WorkflowHumanReview.id == review_id,
                WorkflowHumanReview.tenant_id == tenant_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review card not found",
        )
    if row.status not in {"open"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Review card already {row.status}",
        )
    row.status = body.decision
    row.decision_notes = body.notes
    row.reviewer_user_id = current_user.id
    row.resolved_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return _serialize_human(row)


__all__ = [
    "router",
]
