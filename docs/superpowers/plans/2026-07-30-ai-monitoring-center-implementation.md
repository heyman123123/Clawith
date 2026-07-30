# AI Monitoring Center Implementation Plan

1. Add the `AIInteractionLog` model and migration, then register it with application metadata.
2. Build a privacy-safe monitoring service with request scope propagation, usage normalization, and retention cleanup.
3. Wrap central LLM client creation/calls and set scope from Runtime, legacy caller, compaction, and planning entry points.
4. Add tenant-admin monitoring APIs, aggregate queries, and authorization tests.
5. Add Dashboard API client/types and the bottom monitoring card with expandable details.
6. Add unit/contract coverage and run focused backend checks plus the frontend production build.
