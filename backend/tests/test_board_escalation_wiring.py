"""Production wiring tests for board escalation runtime hooks."""

from __future__ import annotations

from pathlib import Path


def test_governance_completion_source_calls_board_hooks() -> None:
    source = Path("app/services/agent_runtime/governance_completion.py").read_text()
    assert "process_decision_group_agent_output" in source
    assert "process_shareholder_escalation_output" in source
    assert "extract_board_resolution" in source
    assert "extract_escalation_payload" in source
    assert "extract_decision_summary" in source
    assert "await process_decision_group_agent_output(" in source
    assert "await process_shareholder_escalation_output(" in source


def test_worker_registers_governance_terminal_handler() -> None:
    source = Path("app/services/agent_runtime/worker_service.py").read_text()
    assert "from app.services.agent_runtime.governance_completion import" in source
    assert "GovernanceRuntimeCompletionHandler(session_factory=session_factory)" in source


def test_worker_registers_hr_terminal_handler() -> None:
    source = Path("app/services/agent_runtime/worker_service.py").read_text()
    assert "from app.services.agent_runtime.hr_completion import" in source
    assert "HrRuntimeCompletionHandler(session_factory=session_factory)" in source
    hr_source = Path("app/services/agent_runtime/hr_completion.py").read_text()
    assert "process_hr_group_agent_output" in hr_source


def test_governance_handler_routes_shareholder_before_decision_group() -> None:
    source = Path("app/services/agent_runtime/governance_completion.py").read_text()
    shareholder_idx = source.index("process_shareholder_escalation_output")
    decision_idx = source.index("process_decision_group_agent_output")
    assert shareholder_idx < decision_idx
