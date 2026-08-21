"""Orchestrator service package - Intent-Driven Project Orchestrator.

Public API:
  - OrchestratorService: end-to-end propose + create pipeline
  - AtomicCreator: idempotently materialize a Draft into Group + Members + OKR + Tasks
"""
from app.services.orchestrator.draft_schemas import (
    Draft,
    DraftSummary,
    AgentProposal,
    OKRProposal,
    KeyResultProposal,
    TaskProposal,
    GroupProposal,
    ProjectCreated,
)
from app.services.orchestrator.intent_analyzer import IntentAnalyzer
from app.services.orchestrator.agent_selector import AgentSelector
from app.services.orchestrator.group_planner import GroupPlanner

# AtomicCreator imported lazily to avoid circular deps during partial loading
try:
    from app.services.orchestrator.atomic_creator import AtomicCreator
    __all__ = [
        "Draft", "DraftSummary", "AgentProposal", "OKRProposal", "KeyResultProposal",
        "TaskProposal", "GroupProposal", "ProjectCreated",
        "IntentAnalyzer", "AgentSelector", "GroupPlanner", "AtomicCreator",
    ]
except ImportError:
    __all__ = [
        "Draft", "DraftSummary", "AgentProposal", "OKRProposal", "KeyResultProposal",
        "TaskProposal", "GroupProposal", "ProjectCreated",
        "IntentAnalyzer", "AgentSelector", "GroupPlanner",
    ]
