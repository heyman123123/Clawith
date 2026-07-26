from app.models.agent_run import AgentRun


def test_agent_run_has_retry_columns():
    assert hasattr(AgentRun, "retry_of_run_id")
    assert hasattr(AgentRun, "retry_strategy")
    assert hasattr(AgentRun, "failed_retryable")
