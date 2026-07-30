# AI Monitoring Center Design

## Goal

Show tenant administrators every AI/LLM interaction at the bottom of the Dashboard, including the responsible Agent or model, request context, input/output/total token use, latency, and actionable error context.

## Scope and privacy

- The monitor is available to `org_admin` and `platform_admin` only.
- Each event is tenant-scoped. Global models still record the tenant that initiated the call.
- Request messages, tool definitions, model output, and error details are stored after recursive redaction of credentials and bounded to a fixed payload size.
- Events are retained for 30 days. A periodic cleanup removes expired rows.
- Provider-reported token data is preferred; existing project estimation is stored with an explicit `token_source=estimated` fallback.

## Data model

`ai_interaction_logs` is an append-only record with tenant, Agent, model, run/session identifiers, source, status, token counters, duration, sanitized request/response context, and a structured error object. Indexed queries support recent tenant activity, per-Agent use, and error triage.

## Capture boundary

The LLM client factory returns an observing client. It records both `complete` and `stream`, including provider errors. A context-local `AIInteractionScope` supplies tenant, Agent, model, session/run, and source information without changing provider client contracts. Existing LLM call entry points establish that scope before creating a client, including checkpointed Runtime turns, legacy chat/failover turns, compaction, and team planning.

Telemetry persistence is best effort: a database error while writing monitoring data never changes the user-visible result of an LLM call.

## API and Dashboard

`GET /api/ai-monitoring/overview` returns last-24-hour aggregates and recent paginated records for the current tenant. Filters support status, Agent, model, and time range. `GET /api/ai-monitoring/interactions/{id}` returns a record's sanitized request, response, and error detail.

The Dashboard adds a bottom `AI Monitoring Center` card. It presents aggregate Token/call/error counts, a recent-call table, status and token-source labels, and an expandable detail panel. It uses the existing Dashboard styling and does not remove current agent/activity content.

## Verification

- Unit tests prove redaction, provider versus estimated token recording, failure recording, and tenant isolation.
- API tests prove administrator access and forbid ordinary members.
- Frontend contract tests cover the Dashboard monitor data request and visible columns/detail state.
- Backend focused tests, Ruff, frontend contract tests, and production build must pass.
