"""P0 spike: prove AO CLI can be driven from Python via subprocess.

This script is intentionally tiny — its only job is to show that:

1. ``ao --version`` works with the vendored install (or system install).
2. ``ao validate`` accepts the minimal workflow shape Clawith will produce.
3. ``ao plan`` returns a JSON execution plan we can persist into
   ``workflow_run_steps`` without re-parsing the YAML ourselves.

Run it manually with:

    python -m backend.scripts.ao_p0_spike

It prints ``OK`` on success and exits non-zero on any failure. No DB, no
network, no side effects on the project.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import get_settings


SAMPLE_YAML = """\
name: "clawith-p0-spike"
agents_dir: "{agents_dir}"

llm:
  provider: "openai"
  model: "clawith-gateway"

concurrency: 1

inputs:
  - name: subject
    required: true

steps:
  - id: greet
    role: "product/product-manager"
    task: "Reply with a one-line greeting for {{subject}}"
    output: greeting
"""


def _resolve_ao_command(settings) -> list[str]:
    """Return the argv prefix to invoke AO."""
    if settings.AO_VENDOR_DIR:
        vendor = Path(settings.AO_VENDOR_DIR)
        for entry in ("dist/cli.js", "dist/index.js", "bin/ao.js"):
            candidate = vendor / entry
            if candidate.exists():
                return [settings.AO_NODE_BIN, str(candidate)]
    cli = shutil.which(settings.AO_CLI_PATH)
    if cli:
        return [cli]
    raise RuntimeError("AO CLI not found. Set AO_VENDOR_DIR or install `ao` on PATH.")


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=30)


def main() -> int:
    settings = get_settings()
    cmd = _resolve_ao_command(settings)
    print(f"Using AO: {cmd}")

    version_proc = _run([*cmd, "--version"], cwd=Path.cwd(), env={**__import__('os').environ})
    print("Version:", version_proc.stdout.strip() or version_proc.stderr.strip())

    with tempfile.TemporaryDirectory(prefix="clawith-ao-spike-") as tmp:
        tmp_path = Path(tmp)
        agents_dir = settings.AO_AGENTS_DIR or "agency-agents-zh"
        workflow = tmp_path / "spike.yaml"
        workflow.write_text(SAMPLE_YAML.format(agents_dir=agents_dir), encoding="utf-8")

        env = {
            "AO_PROVIDER": settings.AO_PROVIDER,
            "AO_MODEL": settings.AO_MODEL,
            "AO_BASE_URL": settings.AO_BASE_URL,
            "AO_API_KEY": settings.AO_API_KEY,
            "AO_OUTPUT_DIR": str(tmp_path / "out"),
            "AO_HOME": str(tmp_path / "home"),
        }

        validate_proc = _run([*cmd, "validate", str(workflow)], cwd=tmp_path, env=env)
        print("Validate OK:", validate_proc.stdout.strip()[:120])

        plan_proc = _run([*cmd, "plan", "--json", str(workflow)], cwd=tmp_path, env=env)
        try:
            plan = json.loads(plan_proc.stdout)
        except json.JSONDecodeError:
            plan = {"raw_stdout": plan_proc.stdout}
        steps = plan.get("steps") if isinstance(plan, dict) else None
        if not isinstance(steps, list):
            print("Plan output (raw):", plan_proc.stdout[:400])
            raise SystemExit("AO plan output missing steps[] — schema drift")
        print(f"Plan returned {len(steps)} step(s).")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())