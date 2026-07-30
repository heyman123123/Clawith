# Implementation Plan: Team Builder Workflow Confirm + Group Run Failure Recovery

**Goal:** Ship both approved designs in one pass.

**Architecture:** (1) Embed workflow in TeamPlan JSON; revise API; provision applies plan. (2) RuntimeTerminalProductHandler + group_run_resume_jobs + worker scan.

## Task A — Team builder workflow
1. planning.py: TeamPlanWorkflow models, preset helper, validate, attach on generate/fallback
2. service + API revise endpoint
3. provisioning: replace/create workflow from plan
4. Frontend TeamBuilderModal + types + api
5. Tests

## Task B — Group run failure recovery
1. Model + alembic migration
2. resume service (enqueue, classify, notify, probe)
3. RuntimeTerminalProductHandler registration
4. Worker scan every ~60s for due jobs
5. Tests
