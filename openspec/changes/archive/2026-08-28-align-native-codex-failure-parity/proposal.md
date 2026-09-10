# Change: Align native Codex failure parity

## Why

Controlled failure captures show three observable differences between direct
Codex traffic and Codex traffic routed through codex-lb: a native Codex HTTP
fallback can be promoted back to WebSocket, upstream `Retry-After` is lost on
an HTTP 429, and upstream timeout or truncated-stream failures are converted
into a synthetic terminal event. The analyzer also compares only the LB egress
leg with the origin leg, so it does not summarize the end-to-end A/B outcome.

## What Changes

- Preserve downstream HTTP for native Codex Responses requests unless an
  operator explicitly selects upstream WebSocket.
- Preserve a safe upstream `Retry-After` value on a propagated HTTP rejection.
- Preserve native Codex timeout and incomplete-stream lifecycle (connection
  termination without a synthetic terminal) while retaining terminal error
  shaping for non-native clients.
- Add A/B failure-outcome reporting and make `Retry-After` a strict failure
  comparison field.

## Impact

- Affected specs: `responses-api-compat`, `compatibility-tooling`
- Affected code: Responses transport selection, upstream error propagation,
  native stream normalization, traffic analyzer/reporting, and tests
