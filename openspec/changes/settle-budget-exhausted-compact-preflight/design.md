## Context

The HTTP bridge reserves API-key quota before forwarding a Responses request. When that request enters compact handling, the forwarded reservation is passed as an override and the caller does not own settlement; `compact_responses` must finalize successful usage or release the reservation on every terminal failure.

Four compact budget checks currently escape before any existing settlement branch: before freshness, when no freshness reserve remains, after freshness, and before a post-401 forced refresh. Their `ProxyResponseError` reaches an outer handler that only records metadata and a `finally` that only writes the request log.

Inner `_call_compact` budget checks are structurally different: the enclosing retry-loop error handler already settles `upstream_request_timeout` before re-raising.

## Goals / Non-Goals

**Goals:**

- Release the forwarded API-key reservation at every leaking compact preflight budget terminal.
- Preserve the existing account-lease and external error contracts.
- Prove settlement occurs exactly once on both newly fixed preflight exits and already-covered inner exits.

**Non-Goals:**

- Changing compact timeout values, retry eligibility, or account selection.
- Changing API-key quota calculation or stale-reservation cleanup.
- Broadly refactoring compact settlement ownership.

## Decisions

### Settle at each terminal ownership boundary

Each leaking branch will call `_settle_compact_api_key_usage` with `response=None` immediately before `_raise_proxy_budget_exhausted()`. The existing account-lease release remains in its current order.

An unconditional outer cleanup was rejected because success, retry, and upstream-attempt paths already settle with different outcomes; a broad finalizer would obscure ownership and could double-settle or release usage that must be finalized.

### Do not modify inner upstream-call budget exits

The inner `_call_compact` timeout is caught by the retry-loop `ProxyResponseError` handler, which already settles account-neutral `upstream_request_timeout` failures. Adding settlement at the inner raise would perform the lifecycle transition twice.

### Keep the public response unchanged

After settlement, each fixed branch still raises the same `502 upstream_request_timeout` error. The change affects only reservation state.

## Risks / Trade-offs

- **Risk: duplicate settlement** → Keep inner `_call_compact` terminals unchanged and add an exactly-once regression.
- **Risk: one terminal remains uncovered** → Enumerate all four escaping checks in the delta spec and exercise representative outer and inner control-flow paths in tests.
- **Risk: the database release fails** → Reuse `_settle_compact_api_key_usage`, which logs and contains settlement errors so the original timeout still surfaces; stale-reservation cleanup remains the fallback for the unreleased hold.

## Migration Plan

No migration is required. Deploy as a normal application patch. Rollback restores the previous behavior but may reintroduce abandoned reservations until the six-hour stale cleanup threshold is reached.

## Open Questions

None.
