from app.models.agent import Agent
from app.models.governance import DecisionRecord, GovernanceRolePool


def test_agent_has_reusable_column():
    assert hasattr(Agent, "reusable")


def test_governance_models_importable():
    assert GovernanceRolePool.__tablename__ == "governance_role_pools"
    assert DecisionRecord.__tablename__ == "decision_records"
