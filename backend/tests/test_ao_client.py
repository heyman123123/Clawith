from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.ao.client import AOClient


WORKFLOW_YAML = """
name: demo
agents_dir: ./agents
llm:
  provider: openai
  model: demo-model
steps:
  - id: first
    role: analyst
    task: Analyze the input
  - id: second
    role: writer
    task: Write the result
"""


@pytest.fixture
def client() -> AOClient:
    return AOClient()


def test_parse_workflow_validates_and_returns_typed_data(client: AOClient) -> None:
    workflow = client.parse_workflow(WORKFLOW_YAML)

    assert workflow.name == "demo"
    assert workflow.agents_dir == "./agents"
    assert workflow.llm.model_dump() == {"provider": "openai", "model": "demo-model"}
    assert workflow.steps[0].id == "first"


@pytest.mark.parametrize("yaml_text", ["name: demo", "name: demo\nsteps: []"])
def test_parse_workflow_rejects_missing_required_fields(client: AOClient, yaml_text: str) -> None:
    with pytest.raises(ValueError):
        client.parse_workflow(yaml_text)


def test_validate_returns_process_result(monkeypatch: pytest.MonkeyPatch, client: AOClient, tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def fake_run(argv: list[str], *, cwd: Path | None = None, env_overrides: dict[str, str] | None = None) -> Any:
        calls.append((argv, cwd, env_overrides or {}))
        return (0, "valid", "")

    monkeypatch.setattr(client, "_run_subprocess", fake_run)
    path = tmp_path / "workflow.yaml"

    result = client.validate(path, env_overrides={"AO_MODEL": "test-model"})

    assert result.ok is True
    assert result.returncode == 0
    assert result.stdout == "valid"
    assert calls[0][0] == ["ao", "validate", str(path)]
    assert calls[0][1] == path.parent
    assert calls[0][2] == {"AO_MODEL": "test-model"}


def test_validate_reports_failure(monkeypatch: pytest.MonkeyPatch, client: AOClient, tmp_path: Path) -> None:
    monkeypatch.setattr(client, "_run_subprocess", lambda *args, **kwargs: (2, "", "invalid"))

    result = client.validate(tmp_path / "workflow.yaml")

    assert result.ok is False
    assert result.returncode == 2
    assert result.stderr == "invalid"


def test_plan_parses_steps(monkeypatch: pytest.MonkeyPatch, client: AOClient, tmp_path: Path) -> None:
    monkeypatch.setattr(
        client,
        "_run_subprocess",
        lambda *args, **kwargs: (0, json.dumps({"steps": [
            {"id": "a", "role": "analyst", "depends_on": [], "output": "a.md"},
            {"id": "b", "role": "writer", "depends_on": ["a"], "output": "b.md"},
        ]}), ""),
    )

    plans = client.plan(tmp_path / "workflow.yaml")

    assert [step.model_dump() for step in plans] == [
        {"id": "a", "role": "analyst", "depends_on": [], "output": "a.md", "order": 0},
        {"id": "b", "role": "writer", "depends_on": ["a"], "output": "b.md", "order": 1},
    ]


def test_run_builds_input_resume_from_and_output_args(
    monkeypatch: pytest.MonkeyPatch, client: AOClient, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
        calls.append(argv)
        return (0, "done", "")

    monkeypatch.setattr(client, "_run_subprocess", fake_run)
    workflow_path = tmp_path / "workflow.yaml"
    output_dir = tmp_path / "output"

    result = client.run(
        workflow_path,
        inputs={"topic": "AI", "owner": "Ada Lovelace"},
        output_dir=output_dir,
        resume="last",
        from_step="foo",
        watch=True,
    )

    assert result.returncode == 0
    assert result.stdout == "done"
    assert calls == [[
        "ao", "run", str(workflow_path), "--input", "topic=AI", "--input", "owner=Ada Lovelace",
        "--output", str(output_dir), "--resume", "last", "--from", "foo", "--watch",
    ]]
    assert result.output_dir == output_dir


def test_resume_from_step_adds_feedback(monkeypatch: pytest.MonkeyPatch, client: AOClient, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(client, "_run_subprocess", lambda argv, **kwargs: (calls.append(argv) or (0, "ok", "")))

    client.resume_from_step(tmp_path / "workflow.yaml", from_step="review", feedback="Fix citations")

    assert calls == [[
        "ao", "run", str(tmp_path / "workflow.yaml"), "--resume", "last", "--from", "review",
        "--feedback", "Fix citations",
    ]]


def test_list_roles_parses_json(monkeypatch: pytest.MonkeyPatch, client: AOClient) -> None:
    monkeypatch.setattr(client, "_run_subprocess", lambda *args, **kwargs: (0, '[{"id":"analyst"}]', ""))

    assert client.list_roles() == [{"id": "analyst"}]


def test_get_status_reads_metadata(client: AOClient, tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text(
        json.dumps({"state": "running", "completed_steps": ["a"], "total_steps": 2, "last_updated": "2026-07-27T12:00:00Z"}),
        encoding="utf-8",
    )

    status = client.get_status(output_dir)

    assert status.state == "running"
    assert status.completed_steps == ["a"]
    assert status.total_steps == 2
    assert status.last_updated == "2026-07-27T12:00:00Z"


def test_get_status_returns_unknown_when_metadata_is_missing(client: AOClient, tmp_path: Path) -> None:
    status = client.get_status(tmp_path / "missing")

    assert status.state == "unknown"
    assert status.completed_steps == []
    assert status.total_steps == 0
    assert status.last_updated is None
