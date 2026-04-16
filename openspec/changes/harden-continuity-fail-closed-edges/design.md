## Context

The proxy already rewrites most `previous_response_id` continuity failures into retryable contracts, but the remaining gaps sit in two places: bridge-local continuity loss still emits `400 previous_response_not_found`, and owner lookup failures can continue without hard pinning. Both behaviors are inconsistent with the practical goal of preserving run continuity whenever the proxy cannot prove a safe owner.

## Goals / Non-Goals

**Goals:**
- Make continuity-loss edge cases retryable across HTTP bridge, HTTP fallback, and websocket follow-up flows.
- Ensure lookup failures for hard continuity requests fail closed instead of degrading into unpinned recovery.
- Cover the remaining edge cases with regression tests.

**Non-Goals:**
- Introduce durable continuity guarantees beyond the existing owner/alias model.
- Change prompt-cache locality behavior for soft-affinity requests that do not depend on hard continuity anchors.

## Decisions

### Use retryable fail-closed errors for continuity loss
Bridge-local continuity loss should surface as a retryable continuity failure, not as `400 previous_response_not_found`. The proxy already uses `stream_incomplete` for equivalent upstream continuity loss, so the same contract should apply when the bridge itself loses continuity metadata.

Alternative considered: keep raw `400` for “definitive” local misses. Rejected because it leaves clients with two incompatible contracts for the same continuity failure class.

### Fail closed on owner/ring lookup errors for hard continuity
When a request depends on `previous_response_id` or hard bridge continuity keys, lookup failures must not fall back to local recovery without pinning. The proxy should return a retryable `upstream_unavailable` error instead.

Alternative considered: continue current degrade-open behavior. Rejected because it allows continuity fragmentation precisely when the proxy has lost the data needed to enforce owner correctness.

## Risks / Trade-offs

- [Risk] More requests can fail fast during transient owner/ring metadata outages. → Mitigation: failures become retryable and avoid silent continuity forks.
- [Risk] Existing tests and assumptions around raw `previous_response_not_found` need updates. → Mitigation: add targeted regressions before changing runtime behavior.

## Migration Plan

1. Add regression tests for bridge continuity-loss and owner lookup failure paths.
2. Update runtime behavior to emit retryable fail-closed errors for those paths.
3. Run targeted continuity suites and full pytest before merging.

## Open Questions

- None for this change; the desired contract is to eliminate remaining raw continuity leaks and unpinned lookup fallbacks.
