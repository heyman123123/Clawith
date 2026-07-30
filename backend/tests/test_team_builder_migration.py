"""Regression coverage for resuming the intelligent-team schema migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _migration_module() -> ModuleType:
    path = Path(__file__).parents[1] / "alembic/versions/202607301200_add_team_builder_and_group_leader.py"
    spec = importlib.util.spec_from_file_location("team_builder_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Inspector:
    def has_table(self, name: str) -> bool:
        return name == "groups"

    def get_columns(self, name: str) -> list[dict[str, str]]:
        assert name == "groups"
        return [{"name": "leader_participant_id"}]

    def get_foreign_keys(self, name: str) -> list[dict[str, object]]:
        assert name == "groups"
        return [
            {
                "constrained_columns": ["leader_participant_id"],
                "referred_table": "participants",
                "referred_columns": ["id"],
            }
        ]

    def get_indexes(self, name: str) -> list[dict[str, str]]:
        assert name in {"team_build_drafts", "team_provision_jobs", "team_provision_members"}
        return []


class _Operations:
    def __init__(self) -> None:
        self.added_columns: list[object] = []
        self.foreign_keys: list[object] = []
        self.tables: list[str] = []
        self.indexes: list[str] = []

    def add_column(self, *_args: object) -> None:
        self.added_columns.append(_args)

    def create_foreign_key(self, *_args: object, **_kwargs: object) -> None:
        self.foreign_keys.append(_args)

    def create_table(self, name: str, *_args: object) -> None:
        self.tables.append(name)

    def create_index(self, name: str, *_args: object, **_kwargs: object) -> None:
        self.indexes.append(name)


def test_upgrade_resumes_when_group_leader_column_already_exists(monkeypatch) -> None:
    migration = _migration_module()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_inspector", lambda: _Inspector())

    migration.upgrade()

    assert operations.added_columns == []
    assert operations.foreign_keys == []
    assert operations.tables == ["team_build_drafts", "team_provision_jobs", "team_provision_members"]
    assert operations.indexes == [
        "ix_team_build_drafts_tenant_creator_updated",
        "ix_team_provision_jobs_status_updated",
        "ix_team_provision_members_job_status",
    ]


def test_revision_identifier_fits_legacy_alembic_version_column() -> None:
    migration = _migration_module()

    assert len(migration.revision) <= 32
