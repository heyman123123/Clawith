from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api import ao_workflows
from app.core.security import get_current_user
from app.database import get_db


class _FakeDB:
    def __init__(self, scalar_values: list[object | None] | None = None) -> None:
        self.scalar_values = iter(scalar_values or [])

    async def scalar(self, _statement):
        return next(self.scalar_values)

    async def execute(self, _statement):
        raise AssertionError("direct step query should be replaced in this test")

    async def flush(self) -> None:
        return None


class _FakeAOClient:
    def __init__(self) -> None:
        self.parse_calls: list[str] = []
        self.run_calls: list[tuple] = []
        self.status_calls: list[Path] = []

    def parse_workflow(self, yaml_content: str):
        self.parse_calls.append(yaml_content)
        return SimpleNamespace(
            model_dump=lambda mode=None: {
                "name": "demo",
                "agents_dir": "./agents",
                "llm": {"provider": "openai", "model": "demo"},
                "steps": [{"id": "one", "role": "analyst", "task": "分析"}],
            }
        )

    def run(self, yaml_path: Path, **kwargs):
        self.run_calls.append((yaml_path, kwargs))
        output_dir = kwargs["output_dir"]
        return SimpleNamespace(
            returncode=0,
            stdout="started",
            stderr="",
            metadata_path=output_dir / "metadata.json",
            output_dir=output_dir,
        )

    def get_status(self, output_dir: Path):
        self.status_calls.append(output_dir)
        return SimpleNamespace(
            model_dump=lambda mode=None: {
                "state": "running",
                "completed_steps": ["one"],
                "total_steps": 2,
                "last_updated": "2026-07-27T12:00:00Z",
            }
        )


@pytest.fixture
def api_app() -> FastAPI:
    return FastAPI()


@pytest.fixture
def current_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), is_active=True, role="member")


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    user: SimpleNamespace,
    db: _FakeDB,
    json: dict | None = None,
) -> httpx.Response:
    async def override_user():
        return user

    async def override_db() -> AsyncIterator[_FakeDB]:
        yield db

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_parse_endpoint_uses_ao_client(
    api_app: FastAPI,
    current_user: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_app.include_router(ao_workflows.router)
    client = _FakeAOClient()
    monkeypatch.setattr(ao_workflows, "AOClient", lambda: client)

    response = await _request(
        api_app,
        "POST",
        "/api/ao/parse",
        user=current_user,
        db=_FakeDB(),
        json={"yaml_content": "name: demo"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "demo"
    assert response.json()["steps"][0]["id"] == "one"
    assert client.parse_calls == ["name: demo"]


@pytest.mark.asyncio
async def test_run_endpoint_authorizes_tenant_and_returns_run_metadata(
    api_app: FastAPI,
    current_user: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api_app.include_router(ao_workflows.router)
    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(id=workflow_id, tenant_id=current_user.tenant_id)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        tenant_id=current_user.tenant_id,
        yaml_content="name: demo",
        asset_dir_path=str(tmp_path / "run"),
        status="queued",
    )
    db = _FakeDB([workflow, run])
    client = _FakeAOClient()
    monkeypatch.setattr(ao_workflows, "AOClient", lambda: client)
    settings = SimpleNamespace(AO_WORKFLOWS_DIR=str(tmp_path / "workflows"), AO_OUTPUT_DIR=str(tmp_path / "output"))
    monkeypatch.setattr(ao_workflows, "get_settings", lambda: settings)
    marked: list[uuid.UUID] = []

    async def fake_mark(_db, *, workflow_id: uuid.UUID):
        marked.append(workflow_id)
        return run

    monkeypatch.setattr(ao_workflows, "_mark_run_started", fake_mark)

    response = await _request(
        api_app,
        "POST",
        "/api/ao/runs",
        user=current_user,
        db=db,
        json={
            "workflow_id": str(workflow_id),
            "inputs": {"topic": "AO"},
            "resume": "last",
            "from_step": "review",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "workflow_id": str(workflow_id),
        "run_id": str(run.id),
        "returncode": 0,
        "stdout": "started",
        "stderr": "",
        "metadata_path": str(tmp_path / "run" / "metadata.json"),
        "run_dir": str(tmp_path / "run"),
    }
    yaml_path, kwargs = client.run_calls[0]
    assert yaml_path.read_text(encoding="utf-8") == "name: demo"
    assert kwargs["inputs"] == {"topic": "AO"}
    assert kwargs["resume"] == "last"
    assert kwargs["from_step"] == "review"
    assert marked == [workflow_id]


@pytest.mark.asyncio
async def test_status_endpoint_combines_ao_status_and_database_steps(
    api_app: FastAPI,
    current_user: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api_app.include_router(ao_workflows.router)
    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(id=workflow_id, tenant_id=current_user.tenant_id)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        tenant_id=current_user.tenant_id,
        asset_dir_path=str(tmp_path / "run"),
        status="running",
        started_at=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=uuid.uuid4(),
        step_key="one",
        step_order=0,
        role_path="product/analyst",
        status="succeeded",
        depends_on=[],
        output_var="analysis",
        quality_score=95,
        retry_count=0,
    )
    db = _FakeDB([workflow, run])
    client = _FakeAOClient()
    monkeypatch.setattr(ao_workflows, "AOClient", lambda: client)

    async def fake_steps(_db, *, run_id: uuid.UUID):
        assert run_id == run.id
        return [step]

    monkeypatch.setattr(ao_workflows, "_get_run_steps", fake_steps)

    response = await _request(
        api_app,
        "GET",
        f"/api/ao/runs/{workflow_id}/status",
        user=current_user,
        db=db,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == str(workflow_id)
    assert payload["run_id"] == str(run.id)
    assert payload["run_status"] == "running"
    assert payload["ao_status"]["state"] == "running"
    assert payload["steps"] == [
        {
            "id": str(step.id),
            "step_id": "one",
            "order": 0,
            "role": "product/analyst",
            "status": "succeeded",
            "depends_on": [],
            "output": "analysis",
            "quality_score": 95,
            "retry_count": 0,
        }
    ]
    assert client.status_calls == [tmp_path / "run"]     


@pytest.mark.asyncio
async def test_run_endpoint_hides_workflow_from_other_tenant(
    api_app: FastAPI,
    current_user: SimpleNamespace,
) -> None:
    api_app.include_router(ao_workflows.router)

    response = await _request(
        api_app,
        "POST",
        "/api/ao/runs",
        user=current_user,
        db=_FakeDB([None]),
        json={"workflow_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow not found"
