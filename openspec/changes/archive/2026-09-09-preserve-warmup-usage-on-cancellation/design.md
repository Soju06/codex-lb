## Context

`QuotaWarmupService.warm_now` directly awaits reservation finalization inside a
broad cancellation handler. Cancellation of that await reaches the fallback
that fails the reservation with literal zero counts.

## Decision

Await finalization through `_await_cleanup_deferring_cancellation`. Set a
positive `reservation_finalized` marker only after the helper returns normally.
If it reports deferred cancellation, raise it after setting the marker. The
existing cancellation handler applies zero-usage failure only while the marker
is false.

Request logging, warmup-effect refresh, and decision completion remain outside
the owned settlement boundary.

## Risks

- Cancellation waits for the existing settlement operation; this is required
  ownership, not a retry or timeout policy.
- The durable decision remains `executing` and uses existing reconciliation.
- Finalizer exceptions retain the current failure path.
