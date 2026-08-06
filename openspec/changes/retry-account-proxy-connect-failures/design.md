# Design

## Transport provenance

The codebase already carries dispatch provenance on `CodexTransportError` and
`ProxyResponseError`: `retryable_same_contract` is true only when a typed
connector failure proves the upstream request was never dispatched, and
`failure_phase == "connect"` records where the attempt died. This change
reuses that single source of truth instead of introducing a parallel state.

`is_confirmed_pre_dispatch_transport_error` is the one predicate that
authorizes cross-account replay. It requires proven pre-dispatch connect
provenance and explicitly excludes host-wide network loss
(`proxy_network_unavailable`), which keeps its account-neutral process
network recovery path (transport rotation and bounded same-account retry)
rather than penalizing the selected account. TLS verification failures are
stable endpoint configuration errors and never authorize replay.

The native Responses WebSocket connect boundary previously collapsed routed
`CodexTransportError` provenance to the process-network case only; it now
carries the pre-dispatch provenance across the sanitizing conversion. No
proxy URL, credentials, or raw exception text is retained or exposed.

## Retry order

For an HTTP POST, a confirmed pre-dispatch connect failure may try the next
endpoint in the already-resolved proxy pool. This is safe despite the method
being non-idempotent because no request reached upstream. Ambiguous POST
failures retain the existing idempotent-only fallback rule.

If every same-pool endpoint fails before dispatch, raw HTTP/SSE, native
Responses WebSocket, and the HTTP responses bridge exclude the selected
account and use the existing bounded account-selection loop (attempt budget,
request deadline, and a monotonically growing exclusion set). The original
sanitized 502 is retained and returned if no replacement exists, instead of
a generated `no_accounts` response.

## Ownership boundary

Only movable requests can cross accounts. A client or proxy continuation that
requires its owner, an account-scoped uploaded file, a forced single-account
route, or another hard preferred-account contract fails closed on the original
account with the original sanitized failure. Soft prompt-cache and
process-session affinity may move; the failed account's sticky binding is
reallocated so the replacement selection does not immediately loop back.

## Account backoff and resource ordering

A confirmed dead account route is stronger evidence than a generic transient
stream error. It raises the account to the existing transient error-backoff
floor (`record_error_backoff`, shared `ERROR_BACKOFF_THRESHOLD`): 30 seconds
at the floor, exponentially bounded by the existing 300-second cap. It does
not pause, deactivate, rate-limit, or quota-penalize the account, and it
replaces — not stacks onto — the generic single-error health write for the
same failure.

Per-account response-create and stream leases are released before recording
the backoff. The downstream API-key reservation is request-scoped rather than
account-scoped, so an internal pre-dispatch failover keeps that single
reservation alive instead of releasing and racing to reacquire it. Because
account-health mutation must not race a live reservation, keyed requests queue
the dead-route floor and apply it only after the normal terminal finalizer has
settled or released that singular reservation. Startup failure and cancellation
paths release the reservation before draining the same queue. A failed terminal
settlement may fall back to release, but only a confirmed settlement or release
authorizes the queued health write; if both fail, the reservation and backoff
remain pending rather than racing an account-health mutation.

The HTTP bridge transfers settlement ownership to a request state only after
its upstream submit call returns successfully. Outer startup cleanup releases
only the current unowned lifecycle. Each lifecycle generation owns its own
backoff queue, and terminal finalizers drain only that generation after its
settlement is confirmed. A newer retry therefore cannot drain an older failed
settlement's queue, and a late older finalizer cannot drain the newer queue.
Queue entries are claimed before the asynchronous health write so concurrent
terminal and outer cleanup cannot apply the same floor twice.

## Non-goals

- Do not replay ambiguous failures after proxy acceptance, header wait, or
  response-body processing; downstream-visible output always forbids replay.
- Do not treat idle disconnects or downstream idle timeouts as account
  health evidence.
- Do not add endpoint-health persistence or change proxy-pool membership.
- Do not broaden generic `upstream_unavailable` retry classification.
