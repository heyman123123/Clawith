# Agency Orchestrator vendor directory

Clawith invokes AO as a Node CLI subprocess (see `docs/adr/0001-ao-integration.md`).

## Setup

1. Clone / unpack [agency-orchestrator](https://github.com/jnMetaCode/agency-orchestrator) into this directory
   (`backend/vendor/ao`), **or** `npm i -g agency-orchestrator` and leave `AO_VENDOR_DIR` empty.
2. Install Node deps inside the vendor tree if present:
   `cd backend/vendor/ao && npm ci`
3. Enable in env / compose:

```bash
AO_ENABLED=true
AO_VENDOR_DIR=/app/backend/vendor/ao   # container path; optional if `ao` is on PATH
AO_BASE_URL=http://backend:8000/api/llm-gateway   # example
AO_MODEL=clawith-gateway
```

When `AO_ENABLED=false` (default), Clawith still composes YAML and runs the
scheduler/quality/delivery loop; CLI `validate` / `plan` / `run` calls fail
fast with a clear error instead of hanging.
