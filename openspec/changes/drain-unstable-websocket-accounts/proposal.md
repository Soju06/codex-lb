# Drain unstable websocket accounts

## Why

Recent websocket request logs can show repeated upstream open timeouts or early
stream failures for one account while other accounts remain healthy. The proxy
already records these failures distinctly, but HTTP bridge creation and
reattach paths can keep trying the same unstable upstream account before the
balancer has a chance to route around it.

## What Changes

- Treat retryable websocket open timeouts as account instability during HTTP
  bridge create and reconnect attempts.
- Record the failure against the selected account, then retry within the
  request budget using another eligible account when one exists.
- Preserve existing request-log evidence for terminal failures, including
  `upstream_websocket_open_timeout` for open-timeout rows.

## Impact

- Unstable websocket accounts are drained from the current bridge attempt
  instead of repeatedly absorbing retries.
- Existing 401 refresh retry behavior remains unchanged for ordinary auth
  failures.
- No schema or database migration is required.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: define how retryable websocket open timeouts
  are recorded and routed around during HTTP bridge creation and reconnects.
