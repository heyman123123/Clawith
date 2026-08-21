"""Template visibility + approval state enums."""
from __future__ import annotations
from enum import Enum


class TemplateVisibility(str, Enum):
    PUBLIC = "public"                # 全租户可见
    TENANT_PRIVATE = "tenant_private" # 仅本租户可见(需 admin 审核)
    USER_PRIVATE = "user_private"    # 仅创建者可见(默认)


class ApprovalState(str, Enum):
    DRAFT = "draft"                  # LLM 生成,待审核
    APPROVED = "approved"            # 已批准
    REJECTED = "rejected"            # 已拒绝


def visibility_allows_tenant_access(visibility: str, tenant_id_match: bool) -> bool:
    """Whether a template with this visibility is accessible to a same-tenant user."""
    if visibility == TemplateVisibility.PUBLIC.value:
        return True
    if visibility == TemplateVisibility.TENANT_PRIVATE.value:
        return tenant_id_match
    return False  # USER_PRIVATE: only the creator sees it (check separately)


def is_approved_or_draft(approval_state: str) -> bool:
    """Templates in DRAFT or APPROVED state are usable; REJECTED are not."""
    return approval_state in (ApprovalState.APPROVED.value, ApprovalState.DRAFT.value)
