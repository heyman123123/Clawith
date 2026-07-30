"""Group decision-maker services."""

from app.services.group_decision import service as decision_service
from app.services.group_decision.service import GroupDecisionError

__all__ = ["GroupDecisionError", "decision_service"]
