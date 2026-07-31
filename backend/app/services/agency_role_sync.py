"""Fetch and materialize the external agency-agents-zh role catalog at runtime."""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import yaml
from loguru import logger

from app.config import get_settings

UPSTREAM_REPOSITORY = "jnMetaCode/agency-agents-zh"
UPSTREAM_REVISION = "2ecfabf8e944ccdfed63ad8c44d5241290af6977"
UPSTREAM_ARCHIVE_URL = f"https://codeload.github.com/{UPSTREAM_REPOSITORY}/zip/{UPSTREAM_REVISION}"
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MINIMUM_ROLE_COUNT = 200
_LICENSE_NOTICE = """# agency-agents-zh attribution

The role templates in directories prefixed with `agency-` are imported from
`jnMetaCode/agency-agents-zh` at commit `2ecfabf8e944ccdfed63ad8c44d5241290af6977`.

Source license: MIT. Copyright (c) 2025 Michael Sitarzewski (original English
version) and 2026 jnMetaCode (Chinese translation and localization).

The upstream MIT license is included below.

"""


def agency_role_template_root() -> Path:
    """Persistent location for downloaded role templates, outside the image."""
    settings = get_settings()
    return Path(settings.AGENT_DATA_DIR) / "role_templates" / "agency-agents-zh"


def has_agency_role_templates(root: Path | None = None) -> bool:
    """A partial download is treated as absent and repaired before seeding."""
    directory = root or agency_role_template_root()
    count = sum(
        1
        for child in directory.glob("agency-*")
        if child.is_dir() and (child / "meta.yaml").is_file() and (child / "soul.md").is_file()
    )
    return count >= _MINIMUM_ROLE_COUNT


def _parse_role(source_file: Path) -> tuple[dict[str, object], str] | None:
    content = source_file.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(content)
    if match is None:
        return None
    front_matter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(front_matter, dict):
        return None
    name = front_matter.get("name")
    description = front_matter.get("description")
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        return None
    return front_matter, content


def import_agency_roles(source: Path, output_root: Path) -> int:
    """Atomically convert an upstream checkout into Clawith template folders."""
    source = source.resolve()
    if not (source / "LICENSE").is_file():
        raise ValueError(f"not an agency-agents-zh checkout: {source}")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agency-roles-", dir=output_root.parent) as tmp_dir:
        staging = Path(tmp_dir) / "templates"
        staging.mkdir()
        imported = 0
        for source_file in sorted(source.rglob("*.md")):
            relative = source_file.relative_to(source)
            if relative.parts[0] in {"assets", "examples", "scripts", ".github"}:
                continue
            parsed = _parse_role(source_file)
            if parsed is None:
                continue
            front_matter, content = parsed
            slug = "agency-" + "-".join(relative.with_suffix("").parts)
            destination = staging / slug
            destination.mkdir(parents=True)
            meta = {
                "name": front_matter["name"].strip(),
                "description": front_matter["description"].strip(),
                "icon": str(front_matter.get("emoji") or "🤖"),
                "category": relative.parts[0],
                "capability_bullets": [],
                "default_skills": [],
                "default_autonomy_policy": {},
            }
            (destination / "meta.yaml").write_text(
                yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            (destination / "soul.md").write_text(content, encoding="utf-8")
            imported += 1
        if imported < _MINIMUM_ROLE_COUNT:
            raise RuntimeError(f"upstream import produced only {imported} role templates")

        # Staging completes before the cache is replaced, so a failed download
        # never destroys the last known-good runtime role library.
        if output_root.exists():
            shutil.rmtree(output_root)
        shutil.move(str(staging), str(output_root))
        (output_root / "AGENCY_AGENTS_ZH_LICENSE.md").write_text(
            _LICENSE_NOTICE + (source / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return imported


def _download_and_import(output_root: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="agency-source-") as tmp_dir:
        temp_root = Path(tmp_dir)
        archive = temp_root / "agency-agents-zh.zip"
        request = urllib.request.Request(UPSTREAM_ARCHIVE_URL, headers={"User-Agent": "Clawith-role-sync"})
        with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310 - fixed GitHub archive URL
            archive.write_bytes(response.read())
        with zipfile.ZipFile(archive) as source_zip:
            roots = {Path(item.filename).parts[0] for item in source_zip.infolist() if item.filename}
            if len(roots) != 1:
                raise RuntimeError("unexpected upstream archive layout")
            resolved_root = temp_root.resolve()
            if any(
                not (temp_root / item.filename).resolve().is_relative_to(resolved_root)
                for item in source_zip.infolist()
            ):
                raise RuntimeError("unsafe path in upstream archive")
            source_zip.extractall(temp_root)
        checkout = temp_root / next(iter(roots))
        return import_agency_roles(checkout, output_root)


async def ensure_agency_role_templates() -> bool:
    """Download the catalog once when the persistent runtime cache is absent."""
    output_root = agency_role_template_root()
    if has_agency_role_templates(output_root):
        return True
    imported = await asyncio.to_thread(_download_and_import, output_root)
    logger.info("[AgencyRoleSync] Imported {} role templates into {}", imported, output_root)
    return has_agency_role_templates(output_root)
