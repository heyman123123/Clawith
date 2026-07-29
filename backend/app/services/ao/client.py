"""Typed subprocess adapter for Agency Orchestrator workflows."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import Settings, get_settings


class WorkflowLLM(BaseModel):
    """Describe the provider and model AO should use for a workflow."""

    provider: str
    model: str

    @field_validator("provider", "model")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        """Reject blank LLM identifiers because AO cannot resolve them reliably."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class WorkflowStep(BaseModel):
    """Describe one required AO workflow step before handing it to the CLI."""

    model_config = ConfigDict(extra="allow")

    id: str
    role: str
    task: str

    @field_validator("id", "role", "task")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        """Reject blank step fields so malformed DAGs fail before execution."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ParsedWorkflow(BaseModel):
    """Represent the validated workflow fields consumed by Clawith orchestration."""

    name: str
    agents_dir: str
    llm: WorkflowLLM
    steps: list[WorkflowStep]

    @field_validator("name", "agents_dir")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        """Reject blank workflow identifiers because AO requires resolvable values."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ValidationResult(BaseModel):
    """Capture AO validation output without discarding diagnostics."""

    ok: bool
    returncode: int
    stdout: str
    stderr: str


class StepPlan(BaseModel):
    """Expose a normalized AO execution step in deterministic display order."""

    id: str
    role: str
    depends_on: list[str] = Field(default_factory=list)
    output: str | None = None
    order: int


class RunResult(BaseModel):
    """Capture one AO run invocation and its expected filesystem locations."""

    returncode: int
    stdout: str
    stderr: str
    metadata_path: Path | None
    output_dir: Path | None


class RunStatus(BaseModel):
    """Expose stable status fields even when AO metadata is not available yet."""

    state: str
    completed_steps: list[str] = Field(default_factory=list)
    total_steps: int = 0
    last_updated: str | None = None


class AOClient:
    """Invoke the AO CLI through one typed and testable subprocess boundary."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Bind AO settings once so command construction remains deterministic."""
        self.settings = settings or get_settings()

    def parse_workflow(
        self,
        yaml_text: str,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> ParsedWorkflow:
        """Parse and validate required AO YAML fields before starting a process."""
        del env_overrides
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid workflow YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Workflow YAML must contain a mapping")  # noqa: TRY004
        try:
            return ParsedWorkflow.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"Invalid workflow: {exc}") from exc

    def validate(
        self,
        yaml_path: Path,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> ValidationResult:
        """Run AO validation so callers receive its complete diagnostics."""
        returncode, stdout, stderr = self._run_subprocess(
            [*self._cli_prefix(), "validate", str(yaml_path)],
            cwd=yaml_path.parent,
            env_overrides=env_overrides,
        )
        return ValidationResult(ok=returncode == 0, returncode=returncode, stdout=stdout, stderr=stderr)

    def plan(
        self,
        yaml_path: Path,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> list[StepPlan]:
        """Request AO's JSON plan so Clawith can display the resolved DAG."""
        returncode, stdout, stderr = self._run_subprocess(
            [*self._cli_prefix(), "plan", str(yaml_path), "--json"],
            cwd=yaml_path.parent,
            env_overrides=env_overrides,
        )
        self._raise_for_failure("plan", returncode, stderr)
        payload = self._load_json(stdout, "plan")
        raw_steps = payload.get("steps") if isinstance(payload, dict) else payload
        if not isinstance(raw_steps, list):
            raise ValueError("AO plan JSON must contain a steps list")  # noqa: TRY004
        return [StepPlan.model_validate({**step, "order": order}) for order, step in enumerate(raw_steps)]

    def run(
        self,
        yaml_path: Path,
        *,
        inputs: dict[str, str] | None = None,
        output_dir: Path | None = None,
        resume: str | None = None,
        from_step: str | None = None,
        watch: bool = False,
        env_overrides: dict[str, str] | None = None,
    ) -> RunResult:
        """Run an AO workflow while preserving CLI output for audit and recovery."""
        argv = [*self._cli_prefix(), "run", str(yaml_path)]
        for key, value in (inputs or {}).items():
            argv.extend(["--input", f"{key}={value}"])
        if output_dir is not None:
            argv.extend(["--output", str(output_dir)])
        if resume is not None:
            argv.extend(["--resume", resume])
        if from_step is not None:
            argv.extend(["--from", from_step])
        if watch:
            argv.append("--watch")
        returncode, stdout, stderr = self._run_subprocess(
            argv,
            cwd=yaml_path.parent,
            env_overrides=env_overrides,
        )
        resolved_output = output_dir or self._configured_output_dir()
        metadata_path = resolved_output / "metadata.json" if resolved_output is not None else None
        return RunResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            metadata_path=metadata_path,
            output_dir=resolved_output,
        )

    def resume_from_step(
        self,
        yaml_path: Path,
        *,
        from_step: str,
        feedback: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> RunResult:
        """Resume from a failed or approved step because AO owns checkpoint state."""
        argv = [*self._cli_prefix(), "run", str(yaml_path), "--resume", "last", "--from", from_step]
        if feedback is not None:
            argv.extend(["--feedback", feedback])
        returncode, stdout, stderr = self._run_subprocess(
            argv,
            cwd=yaml_path.parent,
            env_overrides=env_overrides,
        )
        output_dir = self._configured_output_dir()
        metadata_path = output_dir / "metadata.json" if output_dir is not None else None
        return RunResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            metadata_path=metadata_path,
            output_dir=output_dir,
        )

    def list_roles(self, *, env_overrides: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """List AO roles as JSON so Clawith can build role selection interfaces."""
        returncode, stdout, stderr = self._run_subprocess(
            [*self._cli_prefix(), "roles", "--json"],
            cwd=self._command_cwd(),
            env_overrides=env_overrides,
        )
        self._raise_for_failure("roles", returncode, stderr)
        payload = self._load_json(stdout, "roles")
        roles = payload.get("roles") if isinstance(payload, dict) else payload
        if not isinstance(roles, list) or not all(isinstance(role, dict) for role in roles):
            raise ValueError("AO roles JSON must contain a list of objects")
        return roles

    def get_status(
        self,
        output_dir: Path,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> RunStatus:
        """Read AO metadata locally so status checks do not launch extra processes."""
        del env_overrides
        metadata_path = output_dir / "metadata.json"
        if not metadata_path.exists():
            return RunStatus(state="unknown")
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid AO metadata at {metadata_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("AO metadata must contain a JSON object")  # noqa: TRY004
        return RunStatus.model_validate(payload)

    def _run_subprocess(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Execute AO consistently so timeout, environment, and capture stay centralized."""
        self._assert_enabled()
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=self._build_env(env_overrides),
            capture_output=True,
            text=True,
            timeout=self.settings.AO_TIMEOUT_SECONDS,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def _assert_enabled(self) -> None:
        """Fail fast when AO CLI is disabled or vendor path is missing."""
        if not bool(self.settings.AO_ENABLED):
            raise RuntimeError(
                "AO_ENABLED=false — set AO_ENABLED=true and install agency-orchestrator "
                "(PATH `ao` or AO_VENDOR_DIR → backend/vendor/ao). See docs/adr/0001-ao-integration.md."
            )
        vendor = (self.settings.AO_VENDOR_DIR or "").strip()
        if vendor and not Path(vendor).exists():
            raise RuntimeError(
                f"AO_VENDOR_DIR={vendor} does not exist. Unpack agency-orchestrator there "
                "or clear AO_VENDOR_DIR to use AO_CLI_PATH on PATH."
            )

    def _cli_prefix(self) -> list[str]:
        vendor_dir = self.settings.AO_VENDOR_DIR
        if vendor_dir:
            vendor_path = Path(vendor_dir)
            candidates = (vendor_path / "bin" / "ao.js", vendor_path / "dist" / "cli.js")
            cli_path = next((path for path in candidates if path.exists()), candidates[0])
            return [self.settings.AO_NODE_BIN, str(cli_path)]
        return [self.settings.AO_CLI_PATH]

    def _build_env(self, env_overrides: dict[str, str] | None) -> dict[str, str]:
        env = os.environ.copy()
        configured = {
            "AO_HOME_DIR": self.settings.AO_HOME_DIR or "",
            "AO_OUTPUT_DIR": self.settings.AO_OUTPUT_DIR or "",
            "AO_AGENTS_DIR": self.settings.AO_AGENTS_DIR or "",
            "AO_WORKFLOWS_DIR": self.settings.AO_WORKFLOWS_DIR or "",
            "AO_PROVIDER": self.settings.AO_PROVIDER,
            "AO_MODEL": self.settings.AO_MODEL,
            "AO_BASE_URL": self.settings.AO_BASE_URL or "",
            "AO_API_KEY": self.settings.AO_API_KEY,
            "AO_CONCURRENCY": str(self.settings.AO_CONCURRENCY),
            "AO_MAX_RETRIES": str(self.settings.AO_MAX_RETRIES),
        }
        env.update({key: str(value) for key, value in configured.items() if value is not None})
        env.update(env_overrides or {})
        return env

    def _configured_output_dir(self) -> Path | None:
        return Path(self.settings.AO_OUTPUT_DIR) if self.settings.AO_OUTPUT_DIR else None

    def _command_cwd(self) -> Path | None:
        if self.settings.AO_HOME_DIR:
            return Path(self.settings.AO_HOME_DIR)
        return None

    @staticmethod
    def _raise_for_failure(command: str, returncode: int, stderr: str) -> None:
        if returncode != 0:
            raise RuntimeError(f"ao {command} failed with exit code {returncode}: {stderr.strip()}")

    @staticmethod
    def _load_json(stdout: str, command: str) -> Any:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ao {command} returned invalid JSON: {exc}") from exc
