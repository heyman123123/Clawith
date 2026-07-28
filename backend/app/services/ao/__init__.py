"""Agency Orchestrator integration package.

This package provides the narrow Python boundary to the vendored or installed
AO CLI. It depends on PyYAML, Pydantic, and the application AO settings, while
keeping AO execution isolated from Clawith business services.
"""

from app.services.ao import asset_writer, dispatcher, quality_engine, scheduler_kickoff, scheduler_tools
from app.services.ao.asset_writer import (
    AssetWriterError,
    sync_workflow_assets,
    write_readme,
    write_step_asset,
)
from app.services.ao.client import (
    AOClient,
    ParsedWorkflow,
    RunResult,
    RunStatus,
    StepPlan,
    ValidationResult,
)
from app.services.ao.dispatcher import DispatchContext
from app.services.ao.dispatcher import dispatch_task_to_role as dispatch_task_to_role_asset
from app.services.ao.quality_engine import QualityVerdict, run_quality_check
from app.services.ao.run_repository import (
    create_run_row,
    get_run_steps,
    mark_run_started,
)
from app.services.ao.scheduler_tools import AOIntegrationError
from app.services.ao.workflow_composer import (
    ComposeResult,
    compose_initial_workflow,
)

__all__ = [
    "AOClient",
    "AOIntegrationError",
    "AssetWriterError",
    "ComposeResult",
    "DispatchContext",
    "ParsedWorkflow",
    "QualityVerdict",
    "RunResult",
    "RunStatus",
    "StepPlan",
    "ValidationResult",
    "asset_writer",
    "compose_initial_workflow",
    "create_run_row",
    "dispatch_task_to_role_asset",
    "dispatcher",
    "get_run_steps",
    "mark_run_started",
    "quality_engine",
    "run_quality_check",
    "scheduler_kickoff",
    "scheduler_tools",
    "sync_workflow_assets",
    "write_readme",
    "write_step_asset",
]