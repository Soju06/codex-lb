## Context

See `proposal.md` for the incident and motivation. The submit-time retry-circuit gate can adopt an `anchor_abandoned` tombstone after request planning has already injected the old durable anchor. It correctly refuses that request, but the refusal currently leaves the same anchor in the live and durable carriers, so the next client retry receives the same injection.

The bridge already has an exact-match anchor invalidation path for an upstream `previous_response_not_found` verdict. That path publishes an admission fence, conditionally clears durable continuity, unregisters the matching alias, clears only matching in-memory state, preserves a concurrently registered successor, and schedules bounded cleanup retries after transient durable failures.

## Goals / Non-Goals

**Goals:**

- Make the existing submit-time tombstone refusal a one-request transition for clients that resend full history.
- Reuse the bridge's fenced, exact-match anchor retirement semantics.
- Keep the abandonment tombstone as the fail-closed guard for delta-only requests until replacement continuity commits.

**Non-Goals:**

- Change the public error status, code, or message returned for the refused request.
- Clear or rewrite client-supplied anchors.
- Change retry-circuit thresholds, tombstone retention, account selection, or owner-unavailable recovery.

## Decisions

### Retire the rejected anchor inside the submit-time refusal path

The refusal path will await the existing exact-match invalidation operation before raising its current error. The rejected ID is already available on the request state, and the tombstone is positive proof that the proxy's injected carrier is dead.

This is preferred to waiting for another request because the next planning pass is the point at which the loop repeats. It is also preferred to clearing only local state because another worker or a later durable lookup would re-inject the same ID.

### Keep the tombstone independent from carrier cleanup

Anchor retirement will not settle or erase the retry-circuit tombstone. A full resend can proceed without the retired anchor because it carries its own context. A delta-only request still needs the tombstone to fail closed rather than start a context-free conversation. Replacement continuity registration remains the existing authority for superseding the tombstone.

### Preserve the first refusal contract

Cleanup is bookkeeping attached to the already selected error. The request that discovered the tombstone still receives the same `404 bridge_previous_response_not_found`. A transient cleanup failure is logged and handled by the existing bounded retry path rather than replacing the public error.

## Risks / Trade-offs

- [Cleanup adds durable I/O to a fail-fast path] -> Reuse the existing conditional clear and its bounded retry behavior; no new database operation or schema is introduced.
- [A sibling may advance continuity during cleanup] -> The existing invalidation path compares both the rejected response ID and durable owner epoch before changing either carrier.
- [Retiring an anchor could tempt an unsafe delta dispatch] -> The abandonment tombstone remains in place, and the existing unanchored-delta planning gate continues returning 404.
- [Cleanup failure can delay recovery] -> The live carrier is cleared for the exact rejected ID and the existing background cleanup retries durable failures without weakening the tombstone guard.

## Migration Plan

Deploy as an application-only change with no configuration or schema migration. Rollback restores the old repeated-refusal behavior but does not corrupt stored continuity. Existing tombstones and durable sessions remain compatible in both directions.
