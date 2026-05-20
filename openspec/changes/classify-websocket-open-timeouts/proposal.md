## Why

WebSocket opening-handshake timeouts currently look like generic upstream
unavailability in request logs. That makes it harder to distinguish account,
network, and upstream-connect phases when live Codex traffic stalls before any
response frame is emitted.

## What Changes

- Classify upstream WebSocket opening-handshake timeouts with a distinct
  request-log error code while preserving the client-facing OpenAI error shape.
- Record safe failure-phase metadata on upstream WebSocket open timeout
  exceptions for downstream logging decisions.
- Expand tier mismatch diagnostics and live request-log snapshots with
  response, model, transport, status, and runtime-correlation fields that do not
  include prompt content or secrets.
- Add focused regression coverage for the timeout classification and diagnostic
  trace fields.

## Capabilities

### New Capabilities

### Modified Capabilities

- `responses-api-compat`: websocket open timeouts remain client-compatible but
  are classified distinctly in persisted request logs.
- `deployment-installation`: live snapshot diagnostics expose safe runtime
  correlation fields for tier and latency investigation.

## Impact

- Backend: upstream WebSocket client error metadata and proxy request-log
  connect-failure settlement.
- Ops diagnostics: live snapshot request-log sections and service-tier mismatch
  log context.
- Tests: focused unit coverage for WebSocket timeout mapping, request-log error
  code settlement, and diagnostic trace fields.
