"""Route and permission-boundary contracts for group workflows."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import group_workflows


def test_workflow_routes_are_registered_before_runtime_group_item_paths() -> None:
    routes = {(method, route.path) for route in group_workflows.router.routes for method in route.methods or ()}

    assert ("GET", "/api/groups/{group_id}/workflow") in routes
    assert ("POST", "/api/groups/{group_id}/workflow/drafts") in routes
    assert ("GET", "/api/groups/{group_id}/workflow/events") in routes


@pytest.mark.asyncio
async def test_non_manager_cannot_use_manager_workflow_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    group = SimpleNamespace(id=uuid.uuid4())
    membership = SimpleNamespace(role="member")

    async def authorize(*_args, **_kwargs):
        return group, membership, SimpleNamespace()

    monkeypatch.setattr(group_workflows.group_chat_service, "authorize_group_member", authorize)

    with pytest.raises(HTTPException) as exc:
        await group_workflows._scope(
            SimpleNamespace(), tenant_id=uuid.uuid4(), group_id=group.id,
            participant_id=uuid.uuid4(), manager=True,
        )

    assert exc.value.status_code == 403
