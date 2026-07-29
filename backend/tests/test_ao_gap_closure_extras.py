"""Tests for AO template skeleton + AO_ENABLED guard."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.services.ao.client import AOClient
from app.services.ao.template_skeleton import skeleton_yaml_for_roles


def test_skeleton_yaml_expands_n_executors_and_deliver():
    text = skeleton_yaml_for_roles(
        workflow_name="demo",
        recommended_roles=["frontend", "backend", "qa"],
    )
    assert "execute_frontend" in text
    assert "execute_backend" in text
    assert "execute_qa" in text
    assert "deliver" in text
    assert "review" in text


def test_ao_client_subprocess_requires_enabled(tmp_path):
    settings = Settings(AO_ENABLED=False)
    client = AOClient(settings=settings)
    with pytest.raises(RuntimeError, match="AO_ENABLED=false"):
        client._run_subprocess(["echo", "hi"], cwd=tmp_path)
