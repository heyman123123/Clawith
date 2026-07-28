from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.task import Task
from app.services.ao import scheduler_kickoff, scheduler_tools


class _FakeDB:
    def __init__(self, *, scalar_value=None) -> None:
        self.scalar_value = scalar_value
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def scalar(self, _statement):
        return self.scalar_value


class _FakeAOClient:
    def __init__(self, *, plan=None, validation_ok=True) -> None:
        self.plan_result = plan or []
        self.validation_ok = validation_ok
        self.calls: list[tuple] = []

    def validate(self, path: Path):
        self.calls.append(("validate", path))
        return SimpleNamespace(ok=self.validation_ok)

    def parse_workflow(self, yaml_content: str):
        self.calls.append(("parse", yaml_content))
        return SimpleNamespace(steps=[SimpleNamespace(id="one"), SimpleNamespace(id="two")])

    def plan(self, path: Path):
        self.calls.append(("plan", path))
        return self.plan_result

    def resume_from_step(self, path: Path, *, from_step: str, feedback: str | None = None):
        self.calls.append(("resume", path, from_step, feedback))
        return SimpleNamespace(returncode=0, output_dir=Path("/tmp/ao-output/demo"))


@pytest.fixture
def ao_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    workflows_dir = tmp_path / "workflows"
    output_dir = tmp_path / "ao-output"
    workflows_dir.mkdir()
    settings = SimpleNamespace(AO_WORKFLOWS_DIR=str(workflows_dir), AO_OUTPUT_DIR=str(output_dir))
    monkeypatch.setattr(scheduler_tools, "get_settings", lambda: settings)
    return workflows_dir, output_dir


def test_ao_parse_workflow_validates_then_parses(
    ao_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_dir, _ = ao_paths
    workflow_id = str(uuid.uuid4())
    yaml_content = "name: demo\nsteps: []\n"
    (workflows_dir / f"{workflow_id}.yaml").write_text(yaml_content, encoding="utf-8")
    client = _FakeAOClient()
    monkeypatch.setattr(scheduler_tools, "AOClient", lambda: client)

    result = scheduler_tools.ao_parse_workflow(workflow_id)

    assert result == {"ok": True, "steps_count": 2}
    assert client.calls == [
        ("validate", workflows_dir / f"{workflow_id}.yaml"),
        ("parse", yaml_content),
    ]


def test_ao_get_execution_plan_returns_normalized_dicts(
    ao_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_dir, _ = ao_paths
    workflow_id = str(uuid.uuid4())
    plan = [
        SimpleNamespace(
            model_dump=lambda: {
                "id": "research",
                "role": "analyst",
                "depends_on": [],
                "output": "research.md",
                "order": 0,
            }
        )
    ]
    client = _FakeAOClient(plan=plan)
    monkeypatch.setattr(scheduler_tools, "AOClient", lambda: client)

    result = scheduler_tools.ao_get_execution_plan(workflow_id)

    assert result == [
        {
            "id": "research",
            "role": "analyst",
            "depends_on": [],
            "output": "research.md",
            "order": 0,
        }
    ]
    assert client.calls == [("plan", workflows_dir / f"{workflow_id}.yaml")]


def test_ao_resume_from_step_returns_process_metadata(
    ao_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_dir, _ = ao_paths
    workflow_id = str(uuid.uuid4())
    client = _FakeAOClient()
    monkeypatch.setattr(scheduler_tools, "AOClient", lambda: client)

    result = scheduler_tools.ao_resume_from_step(workflow_id, "review", "补充引用")

    assert result == {"returncode": 0, "output_dir": "/tmp/ao-output/demo"}
    assert client.calls == [
        ("resume", workflows_dir / f"{workflow_id}.yaml", "review", "补充引用")
    ]


def test_init_workflow_dir_creates_four_stage_directories_and_readmes(
    ao_paths: tuple[Path, Path],
) -> None:
    _, output_dir = ao_paths
    workflow_id = str(uuid.uuid4())

    result = scheduler_tools.init_workflow_dir(workflow_id)

    run_dir = output_dir / workflow_id
    assert result == {"ok": True, "workflow_id": workflow_id, "run_dir": str(run_dir)}
    for directory in ("00-需求", "01-执行", "02-质控", "03-交付"):
        assert (run_dir / directory / "README.md").is_file()


def test_update_workflow_status_writes_status_and_timestamp(
    ao_paths: tuple[Path, Path],
) -> None:
    _, output_dir = ao_paths
    workflow_id = str(uuid.uuid4())

    result = scheduler_tools.update_workflow_status(workflow_id, "active", note="首发开跑")

    status_file = output_dir / workflow_id / "workflow.status"
    payload = scheduler_tools.json.loads(status_file.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["status"] == "active"
    assert payload["status"] == "active"
    assert payload["note"] == "首发开跑"
    assert payload["last_event_at"] == result["last_event_at"]


@pytest.mark.asyncio
async def test_dispatch_task_to_role_creates_task_and_enqueues_group_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    scheduler_agent_id = uuid.uuid4()
    target_agent_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    sender_participant_id = uuid.uuid4()
    target_participant_id = uuid.uuid4()
    db = _FakeDB()
    scope = SimpleNamespace(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        group_id=group_id,
        session_id=session_id,
        scheduler_agent_id=scheduler_agent_id,
        creator_id=creator_id,
        sender_participant_id=sender_participant_id,
        target_participant_id=target_participant_id,
    )
    monkeypatch.setattr(scheduler_tools, "_load_dispatch_scope", lambda *args, **kwargs: scope)
    calls: list[dict] = []

    async def fake_enqueue(_db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(id=uuid.uuid4()), dispatch_kind="single")

    monkeypatch.setattr(scheduler_tools.group_message_service, "enqueue_group_message", fake_enqueue)

    with scheduler_tools.scheduler_tool_context(
        db=db,
        workflow_id=workflow_id,
        actor_agent_id=scheduler_agent_id,
        user_id=creator_id,
    ):
        result = await scheduler_tools.dispatch_task_to_role(
            str(target_agent_id),
            "整理需求基线",
            {"source": "brief.md"},
        )

    task = next(value for value in db.added if isinstance(value, Task))
    assert result["ok"] is True
    assert result["task_id"] == str(task.id)
    assert task.agent_id == target_agent_id
    assert task.project_workflow_id == workflow_id
    assert task.group_id == group_id
    assert calls[0]["mention_participant_ids"] == [target_participant_id]
    assert "整理需求基线" in calls[0]["content"]
    assert calls[0]["project_task_dispatch"] is False


@pytest.mark.asyncio
async def test_send_channel_message_enqueues_public_group_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_id = uuid.uuid4()
    db = _FakeDB()
    scope = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        group_id=group_id,
        session_id=uuid.uuid4(),
        sender_participant_id=uuid.uuid4(),
    )
    monkeypatch.setattr(scheduler_tools, "_load_group_scope", lambda *args, **kwargs: scope)
    calls: list[dict] = []
    message_id = uuid.uuid4()

    async def fake_enqueue(_db, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(id=message_id), dispatch_kind="none")

    monkeypatch.setattr(scheduler_tools.group_message_service, "enqueue_group_message", fake_enqueue)

    with scheduler_tools.scheduler_tool_context(db=db):
        result = await scheduler_tools.send_channel_message(str(group_id), "调度官播报：已开跑")

    assert result == {
        "ok": True,
        "group_id": str(group_id),
        "message_id": str(message_id),
        "dispatch_kind": "none",
    }
    assert calls[0]["content"] == "调度官播报：已开跑"
    assert calls[0]["project_task_dispatch"] is False


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        (scheduler_tools.update_project_status, ("wf", "active"), {"ok": True, "stub": True, "status": "active"}),
        (
            scheduler_tools.audit_skill_application,
            ("wf", "skill-a", "low"),
            {"ok": True, "stub": True, "skill_id": "skill-a", "level": "low"},
        ),
    ],
)
def test_future_phase_tools_return_explicit_stubs(tool, args, expected) -> None:
    assert tool(*args) == expected


def test_scheduler_tools_wrap_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_tools, "AOClient", lambda: SimpleNamespace(plan=lambda _path: (_ for _ in ()).throw(ValueError("bad"))))

    with pytest.raises(scheduler_tools.AOIntegrationError, match="ao_get_execution_plan"):
        scheduler_tools.ao_get_execution_plan(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_run_scheduler_kickoff_invokes_all_first_launch_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid.uuid4()
    group_id = uuid.uuid4()
    leader_agent_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        group_id=group_id,
        group_leader_agent_id=leader_agent_id,
        creator_id=creator_id,
    )
    db = _FakeDB(scalar_value=run)
    calls: list[tuple] = []

    monkeypatch.setattr(
        scheduler_kickoff,
        "init_workflow_dir",
        lambda value: calls.append(("init", value)) or {"ok": True, "run_dir": "/tmp/run"},
    )
    monkeypatch.setattr(
        scheduler_kickoff,
        "ao_get_execution_plan",
        lambda value: calls.append(("plan", value))
        or [
            {"id": "one", "role": "analyst", "depends_on": [], "output": None, "order": 0},
            {"id": "two", "role": "writer", "depends_on": ["one"], "output": None, "order": 1},
        ],
    )
    monkeypatch.setattr(
        scheduler_kickoff,
        "update_workflow_status",
        lambda value, status, *, note=None: calls.append(("status", value, status, note))
        or {"ok": True, "status": status},
    )

    async def fake_send(value: str, content: str):
        calls.append(("message", value, content))
        return {"ok": True, "message_id": "message"}

    monkeypatch.setattr(scheduler_kickoff, "send_channel_message", fake_send)

    async def fake_mark(_db, *, workflow_id: uuid.UUID):
        calls.append(("mark", workflow_id))
        return run

    monkeypatch.setattr(scheduler_kickoff.run_repository, "mark_run_started", fake_mark)

    result = await scheduler_kickoff.run_scheduler_kickoff(db, workflow_id=workflow_id)

    assert result["ok"] is True
    assert result["steps_count"] == 2
    assert result["estimated_minutes"] == 10
    assert ("init", str(workflow_id)) in calls
    assert ("plan", str(workflow_id)) in calls
    assert ("status", str(workflow_id), "active", "首发开跑") in calls
    message_call = next(call for call in calls if call[0] == "message")
    assert message_call[1] == str(group_id)
    assert "2 步骤预计 10 分钟" in message_call[2]
    assert ("mark", workflow_id) in calls
