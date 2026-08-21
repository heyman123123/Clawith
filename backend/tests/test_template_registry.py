"""T5: Template Registry tests."""
from app.services.agent_template.registry.visibility import (
    TemplateVisibility,
    ApprovalState,
    visibility_allows_tenant_access,
    is_approved_or_draft,
)


def test_visibility_enum_values():
    assert TemplateVisibility.PUBLIC.value == "public"
    assert TemplateVisibility.TENANT_PRIVATE.value == "tenant_private"
    assert TemplateVisibility.USER_PRIVATE.value == "user_private"


def test_approval_state_enum_values():
    assert ApprovalState.DRAFT.value == "draft"
    assert ApprovalState.APPROVED.value == "approved"
    assert ApprovalState.REJECTED.value == "rejected"


def test_public_visibility_all_tenants_access():
    assert visibility_allows_tenant_access("public", tenant_id_match=False)
    assert visibility_allows_tenant_access("public", tenant_id_match=True)


def test_tenant_private_requires_match():
    assert visibility_allows_tenant_access("tenant_private", tenant_id_match=True)
    assert not visibility_allows_tenant_access("tenant_private", tenant_id_match=False)


def test_user_private_blocks_all_tenants():
    """USER_PRIVATE check should be done by querying created_by; helper denies by default."""
    assert not visibility_allows_tenant_access("user_private", tenant_id_match=True)
    assert not visibility_allows_tenant_access("user_private", tenant_id_match=False)


def test_approval_state_draft_and_approved_are_usable():
    assert is_approved_or_draft("draft") is True
    assert is_approved_or_draft("approved") is True
    assert is_approved_or_draft("rejected") is False
    assert is_approved_or_draft("garbage") is False


def test_approval_error_has_code():
    from app.services.agent_template.registry.approval import TemplateApprovalError
    e = TemplateApprovalError("not_found", "test")
    assert e.code == "not_found"
    assert "test" in str(e)


def test_persist_generated_template_function_exists():
    """T5.3: persistence helper is importable and has expected signature."""
    from app.services.agent_template.registry.llm_generator import persist_generated_template
    import inspect
    sig = inspect.signature(persist_generated_template)
    params = sig.parameters
    for required in ("tenant_id", "user_id", "name", "role", "system_prompt"):
        assert required in params, f"missing param: {required}"
    assert "template_visibility" in params  # with default
