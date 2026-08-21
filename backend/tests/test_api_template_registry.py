"""Template Registry API surface."""
from app.api.template_registry import router


def test_router_prefix():
    assert router.prefix == "/api/template-registry"


def test_router_has_endpoints():
    paths = sorted(r.path for r in router.routes if hasattr(r, "path"))
    assert "/api/template-registry/pending-approval" in paths
    assert "/api/template-registry/{template_id}/approve" in paths
    assert "/api/template-registry/{template_id}/reject" in paths
    assert "/api/template-registry/browse" in paths
