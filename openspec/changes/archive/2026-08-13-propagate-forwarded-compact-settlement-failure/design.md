## Context

The origin reserves API-key quota before forwarding an HTTP-bridge request and
signs the reservation into the owner request. The owner restores that
reservation as an override, making `owns_reservation` false at the route layer;
`compact_responses` is therefore the only owner-side settlement path.

`_settle_compact_api_key_usage` currently catches every repository,
finalization, or release exception and returns normally. A successful upstream
compact can consequently be reported as successful even though the reservation
is still `reserved`. The source-adapter settlement path already establishes the
local pattern: log the failed accounting write, attempt a release with a fresh
repository, and fail the request explicitly.

## Goals / Non-Goals

**Goals:**

- Make compact settlement persistence failure observable to the forwarded
  caller.
- Release the held reservation through a fresh repository when the fail-safe
  write succeeds.
- Prevent the compact retry loop from treating local usage persistence as an
  upstream or account-health failure.
- Prove the signed owner-forwarded route, failure response, single upstream
  attempt, and final reservation state in one focused integration regression.

**Non-Goals:**

- Changing stale-reservation or quota-cleanup jobs.
- Adding background retries, settings, migrations, or dependencies.
- Changing WebSocket settlement or WebSocket account-health behavior.
- Retrying the upstream compact after it may already have succeeded.

## Decisions

1. **Attempt fail-safe release from a fresh repository, then fail closed.**
   When compact finalization or release raises, the helper logs the original
   exception and attempts `release_usage_reservation` from a newly opened
   repository context. It then raises a `502 usage_settlement_failed`
   `ProxyResponseError` with trusted `failure_phase="usage_settlement"`
   provenance regardless of the fail-safe outcome. A fresh repository avoids
   reusing a transaction that the failed settlement may have rolled back or invalidated.
   If the original commit actually completed before its outcome became
   uncertain, the idempotent release observes a non-`reserved` row and is a
   no-op.

2. **Propagate trusted settlement provenance before retry classification.**
   The compact `except ProxyResponseError` handler checks the internal
   `failure_phase` before any upstream retry, failover, or account-health logic
   and immediately propagates usage-settlement failures. This is stronger than
   matching the externally supplied error code and prevents an upstream
   response from bypassing normal health handling. Raising the public error from
   the central helper also covers settlement calls made from terminal exception
   handlers without duplicating conversion logic at every call site.

3. **Reuse the existing public error convention.**
   The response uses the existing `usage_settlement_failed` code and a generic
   server-error message. Raw database details remain in structured server logs
   and are not exposed to the client.

Alternatives considered:

- Merely re-raising the repository exception would make the failure visible but
  would leave held quota behind even when an immediate fresh-session release
  could recover it, and would expose only a generic owner-forward error.
- An internal exception converted only at the outer request boundary would
  structurally bypass the main retry handler, but settlement is also invoked
  from terminal exception handlers; covering every such site would duplicate
  conversion logic. A single trusted-phase guard keeps the central helper safe.
- Swallowing the exception and relying on stale cleanup preserves the current
  leak window and falsely reports success.
- Retrying the upstream compact is unsafe because the upstream side effect may
  already have completed.

## Risks / Trade-offs

- **Finalization fails but fail-safe release succeeds, so exact usage is not
  charged** → The request fails closed instead of returning success, and the
  held quota is released to avoid a quota deadlock; this matches the existing
  source-settlement policy.
- **Both settlement and fail-safe release fail** → The explicit 502 remains
  visible and the reservation stays held for the existing cleanup policy; both
  failures are logged without adding a new background mechanism.
- **A settlement error is accidentally treated as upstream failure** → The
  trusted `failure_phase` guard runs before the inner retry/health logic, and the
  integration regression asserts a single upstream call and no health-error handling.
