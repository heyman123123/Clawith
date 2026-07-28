"""Evolution engine + one-step role rollback (P3).

Pipeline
--------
1. :func:`seed_role_baseline` — first-time capture of an agent's soul.
   Called from :func:`seed_role_baselines_for_workflow_run` once after
   project provisioning so every workflow role has a v1 before the first
   AO step runs.

2. :func:`record_quality_step_passed` — invoked after a quality step
   passes. Stores the verdict and returns the prior ``is_current``
   version id so callers can decide whether to evolve.

3. :func:`evolve_role` — apply a new soul (or prompt patch) for a role:
   insert a new ``AgentRoleVersion`` with ``is_current=True`` after
   atomically marking the previous one ``is_current=False``. Records an
   ``AgentEvolutionRecord`` so the regression harness can replay later.

4. :func:`rollback_role_one_step` — restore the previous ``is_current``
   version of a role. One step only (P3 scope). Returns the new
   ``is_current`` version id.

All mutators work in a single transaction via the caller's session — the
engine never opens its own DB connection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.evolution import AgentEvolutionRecord, AgentRoleVersion
from app.services.ao.quality_rules import QualityVerdict


@dataclass(slots=True)
class RoleVersionSnapshot:
    id: uuid.UUID
    version_no: int
    soul_md: str
    source: str
    quality_score: int | None
    summary: str | None


@dataclass(slots=True)
class EvolutionOutcome:
    new_version_id: uuid.UUID | None
    record_id: uuid.UUID | None
    prior_version_id: uuid.UUID | None
    evolved: bool


async def get_current_version(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
) -> AgentRoleVersion | None:
    return await db.scalar(
        select(AgentRoleVersion).where(
            AgentRoleVersion.agent_id == agent_id,
            AgentRoleVersion.is_current.is_(True),
        )
    )


async def get_version_history(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
) -> list[RoleVersionSnapshot]:
    rows = (
        await db.execute(
            select(AgentRoleVersion)
            .where(AgentRoleVersion.agent_id == agent_id)
            .order_by(AgentRoleVersion.version_no.asc())
        )
    ).scalars().all()
    return [
        RoleVersionSnapshot(
            id=row.id,
            version_no=row.version_no,
            soul_md=row.soul_md,
            source=row.source,
            quality_score=row.quality_score,
            summary=row.summary,
        )
        for row in rows
    ]


async def seed_role_baseline(
    db: AsyncSession,
    *,
    agent: Agent,
    soul_md: str,
    summary: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> EvolutionOutcome:
    """Capture the v1 baseline for an agent's soul.

    Idempotent: if a baseline already exists we leave it alone and return
    ``evolved=False`` so the caller can detect a no-op.
    """
    existing = await db.scalar(
        select(AgentRoleVersion).where(AgentRoleVersion.agent_id == agent.id)
    )
    if existing is not None:
        return EvolutionOutcome(
            new_version_id=None,
            record_id=None,
            prior_version_id=existing.id if existing.is_current else None,
            evolved=False,
        )

    next_version_no = 1
    record = AgentEvolutionRecord(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id or uuid.uuid4(),
        agent_id=agent.id,
        trigger_source="baseline_seed",
        kind="baseline",
        from_version_id=None,
        to_version_id=None,
        rationale=summary or "Initial baseline.",
        record_metadata={"origin": "seed_role_baseline"},
        created_by_user_id=actor_user_id,
    )
    db.add(record)
    await db.flush()

    new_version = AgentRoleVersion(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        version_no=next_version_no,
        soul_md=soul_md,
        source="baseline",
        evolution_record_id=record.id,
        quality_score=None,
        summary=summary,
        is_current=True,
        created_by_user_id=actor_user_id,
    )
    db.add(new_version)
    await db.flush()
    return EvolutionOutcome(
        new_version_id=new_version.id,
        record_id=record.id,
        prior_version_id=None,
        evolved=True,
    )


async def record_quality_step_passed(
    db: AsyncSession,
    *,
    agent: Agent,
    verdict: QualityVerdict,
    trigger_ref_id: uuid.UUID | None,
    summary: str | None = None,
) -> RoleVersionSnapshot | None:
    """Update the current role version's quality score after a step passes.

    Returns the updated snapshot (or ``None`` if the agent has no baseline
    yet — callers should treat this as a soft signal that the workflow
    predates the evolution engine).
    """
    current = await get_current_version(db, agent_id=agent.id)
    if current is None:
        logger.info(
            "[Evolution] no baseline for agent={} — skipping score update",
            agent.id,
        )
        return None
    score_before = current.quality_score
    if score_before is None or score_before < verdict.score:
        current.quality_score = verdict.score
        current.summary = summary or current.summary
        await db.flush()
    return RoleVersionSnapshot(
        id=current.id,
        version_no=current.version_no,
        soul_md=current.soul_md,
        source=current.source,
        quality_score=current.quality_score,
        summary=current.summary,
    )


async def evolve_role(
    db: AsyncSession,
    *,
    agent: Agent,
    new_soul_md: str,
    rationale: str,
    trigger_source: str,
    trigger_ref_id: uuid.UUID | None = None,
    quality_score_before: int | None = None,
    quality_score_after: int | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> EvolutionOutcome:
    """Insert a new role version (vN+1) and mark it current.

    The previous ``is_current`` row is flipped to ``False`` in a single
    UPDATE so callers don't need to wrap this in a transaction.
    """
    current = await get_current_version(db, agent_id=agent.id)
    next_version_no = (current.version_no + 1) if current else 1

    record = AgentEvolutionRecord(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id or uuid.uuid4(),
        agent_id=agent.id,
        trigger_source=trigger_source,
        trigger_ref_id=trigger_ref_id,
        kind="evolution",
        from_version_id=current.id if current else None,
        to_version_id=None,
        quality_score_before=quality_score_before,
        quality_score_after=quality_score_after,
        rationale=rationale,
        record_metadata={"origin": "evolve_role"},
        created_by_user_id=actor_user_id,
    )
    db.add(record)
    await db.flush()

    new_version = AgentRoleVersion(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        version_no=next_version_no,
        soul_md=new_soul_md,
        source="evolution",
        evolution_record_id=record.id,
        quality_score=quality_score_after,
        summary=rationale,
        is_current=False,
        created_by_user_id=actor_user_id,
    )
    db.add(new_version)
    await db.flush()

    await db.execute(
        update(AgentRoleVersion)
        .where(AgentRoleVersion.agent_id == agent.id, AgentRoleVersion.id != new_version.id)
        .values(is_current=False)
    )
    new_version.is_current = True
    new_version.evolution_record_id = record.id
    record.to_version_id = new_version.id
    await db.flush()

    return EvolutionOutcome(
        new_version_id=new_version.id,
        record_id=record.id,
        prior_version_id=current.id if current else None,
        evolved=True,
    )


async def rollback_role_one_step(
    db: AsyncSession,
    *,
    agent: Agent,
    rationale: str,
    trigger_source: str = "manual_rollback",
    actor_user_id: uuid.UUID | None = None,
) -> EvolutionOutcome:
    """Restore the previous role version and log a rollback record.

    One step only. If there is nothing to roll back to (current is v1),
    returns ``evolved=False`` with ``prior_version_id=None``.
    """
    current = await get_current_version(db, agent_id=agent.id)
    if current is None:
        return EvolutionOutcome(
            new_version_id=None,
            record_id=None,
            prior_version_id=None,
            evolved=False,
        )

    history = (
        await db.execute(
            select(AgentRoleVersion)
            .where(AgentRoleVersion.agent_id == agent.id)
            .order_by(AgentRoleVersion.version_no.desc())
        )
    ).scalars().all()
    if len(history) < 2:
        return EvolutionOutcome(
            new_version_id=None,
            record_id=None,
            prior_version_id=current.id,
            evolved=False,
        )

    previous = history[1]
    await db.execute(
        update(AgentRoleVersion)
        .where(AgentRoleVersion.agent_id == agent.id)
        .values(is_current=False)
    )
    previous.is_current = True
    record = AgentEvolutionRecord(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id or uuid.uuid4(),
        agent_id=agent.id,
        trigger_source=trigger_source,
        kind="rollback",
        from_version_id=current.id,
        to_version_id=previous.id,
        quality_score_before=current.quality_score,
        quality_score_after=previous.quality_score,
        rationale=rationale,
        record_metadata={
            "origin": "rollback_role_one_step",
            "rolled_back_from_version": current.version_no,
            "restored_version": previous.version_no,
        },
        created_by_user_id=actor_user_id,
    )
    db.add(record)
    await db.flush()

    return EvolutionOutcome(
        new_version_id=previous.id,
        record_id=record.id,
        prior_version_id=current.id,
        evolved=True,
    )


__all__ = [
    "EvolutionOutcome",
    "RoleVersionSnapshot",
    "evolve_role",
    "get_current_version",
    "get_version_history",
    "record_quality_step_passed",
    "rollback_role_one_step",
    "seed_role_baseline",
]
