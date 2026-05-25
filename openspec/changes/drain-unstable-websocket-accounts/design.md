## Context

The live 120-minute snapshot showed fast readiness checks and a healthy
container, but repeated websocket-instability rows grouped by account hash:
`stream_incomplete` after established streams and
`upstream_websocket_open_timeout` during connection setup. Existing specs
already require pending websocket drops to penalize affected accounts, and the
HTTP bridge had tests describing account failover after retryable open timeouts.

The implementation gap is narrower than a new routing subsystem: in bridge
create and reconnect flows, retryable open-timeout handling must run around the
first upstream websocket open attempt, not only around a later 401 refresh retry.

## Decisions

- Preserve the client-facing `upstream_unavailable` contract for terminal
  websocket open timeouts.
- Treat `ProxyResponseError(failure_phase="websocket_open_timeout",
  retryable_same_contract=True)` as account instability while request budget
  remains.
- Record the selected account failure through the existing stream-error /
  load-balancer path before retrying selection.
- Exclude the timed-out account from the current bridge attempt after the
  bounded same-account retry is consumed.
- Keep the fix scoped to HTTP bridge create/reconnect connect phases; established
  stream `stream_incomplete` handling continues through the existing lifecycle
  failure path.

## Risks

- Over-draining could reduce account availability if a single transient timeout
  immediately ejects an account. The implementation only excludes the account
  from the current bridge attempt and uses existing account failure counters for
  longer-lived routing pressure.
- Reordering error handling could disturb 401 refresh recovery. Focused tests
  cover that the open-timeout branch runs before the auth-refresh branch while
  ordinary 401 behavior remains unchanged.
