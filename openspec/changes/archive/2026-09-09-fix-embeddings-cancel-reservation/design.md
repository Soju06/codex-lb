## Context

`_source_embeddings_response` owns the API-key reservation it creates immediately before forwarding to the selected OpenAI-compatible model source. It releases on `ModelSourceForwardingError` and settles normal responses, but task cancellation is a `BaseException` that bypasses both paths. `v1_embeddings` only awaits the helper, so no outer layer owns fallback release. The stale-reservation sweeper is an age-based orphan backstop, not request-lifecycle settlement.

The adjacent source-chat implementation already provides `_release_reservation_deferring_cancellation`, which shields and drains the owned release operation despite repeated cancellation delivery. Source audio transcription has the same current exception shape, but this candidate is deliberately limited to embeddings.

## Goals / Non-Goals

**Goals:**

- Release the embeddings reservation exactly once when cancellation interrupts upstream forwarding.
- Complete release despite cancellation already being active, then re-raise the original `CancelledError`.
- Leave existing forwarding-error, success, missing-usage, and settlement behavior unchanged.

**Non-Goals:**

- Changing source audio transcription or any other proxy flow.
- Changing reservation persistence, idempotency, stale-reclamation timing, or request logging.
- Adding a new cleanup abstraction when the established helper already encodes the required semantics.

## Decisions

1. **Catch cancellation beside the forwarding await.** Reservation ownership begins before `forward_source_embeddings`; handling cancellation at that exact seam avoids duplicating ownership in the route and cannot affect pre-reservation failures.
2. **Use the established cancellation-deferring release helper.** A raw `await _release_reservation` can itself be interrupted by the already-cancelled task. The existing helper shields an owned cleanup task and waits through repeated cancellation, while reservation transition idempotency keeps release exactly once.
3. **Use a route-level deterministic regression.** The test starts a real source-routed `/v1/embeddings` request, waits on an upstream-entry event, cancels the request task, and inspects the exact persisted reservation. This distinguishes direct cancellation from setup failures and does not rely on sleeps or stale reclamation.

## Risks / Trade-offs

- **Release persistence can delay cancellation propagation.** → This is required ownership cleanup and uses the existing bounded database/session behavior; the original cancellation is re-raised immediately after release completes.
- **Audio transcription remains exposed to the same shape.** → Record it as an adjacent follow-up risk; do not broaden this candidate beyond the confirmed embeddings scope.
- **Cancellation after forwarding returns enters later settlement work.** → This fix targets cancellation while upstream forwarding is in flight, matching the confirmed candidate and regression; existing settlement semantics remain unchanged.
