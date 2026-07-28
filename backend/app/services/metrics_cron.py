"""Nightly cron for workflow metrics (P6).

This module schedules a background task that once per day recomputes
the past N days of :class:`WorkflowMetricDaily` rows for every tenant.
We use :mod:`asyncio`'s running-loop approach instead of a third
party scheduler — Clawith already relies on in-process async loops and
adding APScheduler would balloon the dependency surface for a single
daily call.

The cron can be installed via :func:`install_metrics_cron`, which is
called from the FastAPI ``lifespan`` context. It can be uninstalled via
:func:`uninstall_metrics_cron` (used by the test harness).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from typing import Any, Callable

from loguru import logger
from sqlalchemy import select

from app.models.tenant import Tenant
from app.services.workflow_metrics import backfill_recent

_SESSION_FACTORY_KEY = "session_factory"
_DEFAULT_RUN_AT = time(hour=2, minute=30)


class MetricsCronState:
    """Mutable handle to the running cron; :class:`install_metrics_cron`
    creates one and the test fixtures reach for it directly."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        run_at: time = _DEFAULT_RUN_AT,
        backfill_days: int = 7,
    ) -> None:
        self.session_factory = session_factory
        self.run_at = run_at
        self.backfill_days = backfill_days
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="metrics-cron")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._sleep_until_next_run()
                if self._stopping.is_set():
                    return
                await self._run_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("metrics cron iteration failed: {}", exc)

    async def _sleep_until_next_run(self) -> None:
        now = datetime.now()
        target = datetime.combine(now.date(), self.run_at)
        if target <= now:
            from datetime import timedelta

            target = target + timedelta(days=1)
        await asyncio.wait_for(self._stopping.wait(), timeout=max(0.0, (target - now).total_seconds()))
        if self._stopping.is_set():
            return

    async def _run_once(self) -> None:
        logger.info("metrics cron: starting nightly backfill ({} days)", self.backfill_days)
        await run_metrics_backfill_for_all_tenants(
            self.session_factory, days=self.backfill_days
        )

    async def run_once_now(self) -> int:
        """Helper for tests + admin endpoint: bypass scheduling."""
        return await run_metrics_backfill_for_all_tenants(
            self.session_factory, days=self.backfill_days
        )


_state: MetricsCronState | None = None


async def run_metrics_backfill_for_all_tenants(
    session_factory: Callable[[], Any],
    *,
    days: int = 7,
) -> int:
    """Run a backfill for every tenant visible to ``session_factory``."""

    processed = 0
    async with session_factory() as session:  # type: ignore[func-returns-value]
        tenant_ids = list(
            (await session.scalars(select(Tenant.id))).all()
        )
    for tenant_id in tenant_ids:
        async with session_factory() as session:  # type: ignore[func-returns-value]
            try:
                rows = await backfill_recent(session, tenant_id, days=days)
                processed += len(rows)
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("metrics backfill failed for tenant {}: {}", tenant_id, exc)
    return processed


def install_metrics_cron(
    session_factory: Callable[[], Any],
    *,
    run_at: time = _DEFAULT_RUN_AT,
    backfill_days: int = 7,
) -> MetricsCronState:
    """Install the cron at module-level. Returns the state for tests."""

    global _state
    if _state is not None:
        return _state
    _state = MetricsCronState(
        session_factory=session_factory, run_at=run_at, backfill_days=backfill_days
    )
    try:
        loop = asyncio.get_event_loop()  # noqa: F841 - bound to module-level state
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # FastAPI lifespan invokes us with a running loop, so we can
        # schedule the task immediately.
        loop.create_task(_state.start())
    return _state


async def uninstall_metrics_cron() -> None:
    global _state
    if _state is None:
        return
    await _state.stop()
    _state = None


def get_metrics_cron_state() -> MetricsCronState | None:
    return _state


__all__ = [
    "MetricsCronState",
    "get_metrics_cron_state",
    "install_metrics_cron",
    "run_metrics_backfill_for_all_tenants",
    "uninstall_metrics_cron",
]
