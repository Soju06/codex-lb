## Why

The existing account probe wakes the upstream limiter but returns at HTTP headers.
An HTTP 200 stream can still fail or close before completion, so it cannot verify
that the selected account can complete a simple request.

## What Changes

- Add optional `mode: wakeup | completion`, defaulting to the existing wakeup behavior.
- In completion mode, send one fixed minimal prompt through the selected account's
  resolved proxy route, consume a bounded SSE stream, and require `response.completed`.
- Return optional model, outcome, latency and sanitized error fields alongside
  the existing actual HTTP status and before/after account and usage snapshots.
- Never settle failed or incomplete completion tests as successful probes.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: bounded completion verification on the existing account probe.

## Impact

Accounts API, schemas, service, a small account probe sender and focused tests.
The existing stream client adds optional event/error-read limits and captures
actual HTTP status on its route trace, preserving defaults for existing callers.
No frontend, retry-policy, database, deployment, dependency or setting changes.
