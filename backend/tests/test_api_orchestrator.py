"""T2.7: API module imports + endpoint structure."""
import pytest
from app.api.orchestrator import router


def test_router_has_expected_endpoints():
    paths = {route.path for route in router.routes}
    assert "/api/orchestrator/drafts" in paths
    assert "/api/orchestrator/drafts/{draft_id}/approve" in paths
    assert "/api/orchestrator/drafts/{draft_id}/create" in paths


def test_router_prefix_is_orchestrator():
    assert router.prefix == "/api/orchestrator"


def test_router_tag_is_orchestrator():
    assert "orchestrator" in router.tags
