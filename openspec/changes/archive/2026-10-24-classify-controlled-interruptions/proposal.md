## Why

Codex interrupts an active Responses generation when a subagent message arrives. Upstream reports `response.incomplete` with reason `interrupted`; the dashboard currently labels this controlled terminal as Error.

## What Changes

- Record WebSocket Responses controlled interruptions as the existing non-error `cancelled` status, including HTTP bridge requests settled through that path.
- Preserve the upstream reason, partial usage, and downstream event unchanged.
- Present legacy request-log rows with status `error` and code `interrupted` as Cancelled and keep list filters and counts consistent.
- Retain historical aggregate buckets without backfill, following the existing historical error-metric contract.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: classify the exact controlled interruption terminal without changing other failures or settlement.
- `usage-error-metrics`: include controlled interruptions in cancellation presentation and new-record accounting.

## Impact

WebSocket terminal classification, request-log mapping and filtering, and focused regression tests. Reuses existing dashboard styling and status; no settings, schema changes, dependencies, or live data writes.
