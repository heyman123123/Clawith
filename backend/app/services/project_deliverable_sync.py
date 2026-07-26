"""Sync completed project-task artifacts from private Agent workspace into Group workspace."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_tool_execution import AgentToolExecution
from app.models.task import Task
from app.services.workspace_collaboration import normalize_workspace_path


_WORKSPACE_REF_RE = re.compile(
    r"^workspace://(?P<agent_id>[0-9a-fA-F-]{36})/(?P<path>.+)$"
)


@dataclass(frozen=True, slots=True)
class SyncedDeliverable:
    source_ref: str
    group_path: str


def parse_workspace_artifact_ref(reference: str) -> tuple[uuid.UUID, str] | None:
    """Parse workspace://{agent_id}/{path} into agent id + storage-relative path."""
    match = _WORKSPACE_REF_RE.match((reference or "").strip())
    if match is None:
        try:
            parsed = urlsplit(reference)
        except ValueError:
            return None
        if parsed.scheme != "workspace" or not parsed.netloc:
            return None
        try:
            agent_id = uuid.UUID(parsed.netloc)
        except ValueError:
            return None
        path = normalize_workspace_path(unquote(parsed.path or ""))
        if not path:
            return None
        return agent_id, path
    try:
        agent_id = uuid.UUID(match.group("agent_id"))
    except ValueError:
        return None
    path = normalize_workspace_path(unquote(match.group("path")))
    if not path:
        return None
    return agent_id, path


def deliverable_group_path(*, task_id: uuid.UUID, relative_path: str) -> str:
    """Place synced files under deliverables/{task_id}/... in the group workspace."""
    safe = normalize_workspace_path(relative_path)
    return f"deliverables/{task_id}/{safe}"


async def collect_run_workspace_artifact_refs(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[str]:
    """Collect unique workspace:// artifact refs from succeeded tool executions."""
    result = await db.execute(
        select(AgentToolExecution).where(
            AgentToolExecution.tenant_id == tenant_id,
            AgentToolExecution.run_id == run_id,
            AgentToolExecution.status == "succeeded",
        )
    )
    refs: list[str] = []
    for execution in result.scalars().all():
        metadata = execution.result_metadata if isinstance(execution.result_metadata, dict) else {}
        raw = metadata.get("artifact_refs")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str) and item.startswith("workspace://"):
                refs.append(item)
    return list(dict.fromkeys(refs))


async def sync_task_deliverables_to_group(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    task: Task,
    run_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> list[SyncedDeliverable]:
    """Copy private workspace artifacts produced by a task run into the group workspace.

    Already-group-native files (written via group_write_workspace_file) are left alone;
    this only mirrors workspace:// private refs so the Group Workspace tab shows deliverables.
    """
    from loguru import logger

    from app.services.group_file_service import (
        GroupFileServiceError,
        write_workspace_binary_file,
        write_workspace_file,
    )
    from app.services.storage import agent_storage_key, get_storage_backend, guess_content_type

    if task.group_id is None:
        return []

    refs = await collect_run_workspace_artifact_refs(
        db, tenant_id=tenant_id, run_id=run_id
    )
    if not refs:
        return []

    storage = get_storage_backend()
    synced: list[SyncedDeliverable] = []
    for reference in refs:
        parsed = parse_workspace_artifact_ref(reference)
        if parsed is None:
            continue
        agent_id, relative_path = parsed
        if agent_id != task.agent_id:
            continue
        source_key = agent_storage_key(agent_id, relative_path)
        try:
            version = await storage.get_version(source_key)
            if not version.exists or version.is_dir:
                continue
            content = await storage.read_bytes(source_key)
        except Exception as exc:
            logger.warning(
                "[DeliverableSync] skip unreadable artifact {} ({})",
                reference,
                type(exc).__name__,
            )
            continue

        group_path = deliverable_group_path(task_id=task.id, relative_path=relative_path)
        content_type = guess_content_type(relative_path)
        try:
            if content_type.startswith("text/") or relative_path.endswith(
                (".md", ".txt", ".json", ".csv", ".yml", ".yaml", ".xml", ".html")
            ):
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    await write_workspace_binary_file(
                        db,
                        tenant_id=tenant_id,
                        group_id=task.group_id,
                        actor_participant_id=actor_participant_id,
                        path=group_path,
                        content=content,
                        content_type=content_type or "application/octet-stream",
                        session_id=task.session_id,
                    )
                else:
                    await write_workspace_file(
                        db,
                        tenant_id=tenant_id,
                        group_id=task.group_id,
                        actor_participant_id=actor_participant_id,
                        path=group_path,
                        content=text,
                        session_id=task.session_id,
                    )
            else:
                await write_workspace_binary_file(
                    db,
                    tenant_id=tenant_id,
                    group_id=task.group_id,
                    actor_participant_id=actor_participant_id,
                    path=group_path,
                    content=content,
                    content_type=content_type or "application/octet-stream",
                    session_id=task.session_id,
                )
        except GroupFileServiceError as exc:
            logger.warning(
                "[DeliverableSync] failed to write {} → {}: {}",
                reference,
                group_path,
                exc,
            )
            continue
        synced.append(SyncedDeliverable(source_ref=reference, group_path=group_path))

    if synced:
        logger.info(
            "[DeliverableSync] task={} synced {} file(s) into group {}",
            task.id,
            len(synced),
            task.group_id,
        )
    return synced


__all__ = [
    "SyncedDeliverable",
    "collect_run_workspace_artifact_refs",
    "deliverable_group_path",
    "parse_workspace_artifact_ref",
    "sync_task_deliverables_to_group",
]
