## Context

`_source_audio_transcription_response` owns the API-key reservation it creates immediately before forwarding to the selected OpenAI-compatible model source. It releases on `ModelSourceForwardingError` and settles normal responses, but task cancellation is a `BaseException` that bypasses both paths. `v1_audio_transcriptions` only awaits the helper, so no outer layer owns fallback release. The stale-reservation sweeper is an age-based orphan backstop, not request-lifecycle settlement.

The source-embeddings implementation already handles the same ownership boundary with `_release_reservation_deferring_cancellation`, which shields and drains the owned release operation despite repeated cancellation delivery. This change applies that established pattern only to source-routed audio transcription.

## Goals / Non-Goals

**Goals:**

- Release the audio transcription reservation exactly once when cancellation interrupts upstream forwarding.
- Complete release despite cancellation already being active, then re-raise the original `CancelledError`.
- Restore the reserved quota immediately and leave existing forwarding-error, success, missing-usage, and settlement behavior unchanged.
- Keep cleanup-failure diagnostics neutral about the reason for request cancellation.

**Non-Goals:**

- Changing non-stream Responses, streaming chat, embeddings, or any other proxy flow.
- Changing reservation persistence, idempotency, stale-reclamation timing, or request-log taxonomy.
- Adding a general cleanup abstraction when the established helper already encodes the required semantics.

## Decisions

1. **Catch cancellation beside the forwarding await.** Reservation ownership begins before `forward_source_audio_transcription`; handling cancellation at that exact seam avoids duplicating ownership in the route and cannot affect pre-reservation failures. An outer route `finally` was rejected because it would compete with normal settlement ownership.
2. **Use the established cancellation-deferring release helper.** A raw `await _release_reservation` can itself be interrupted by the already-cancelled task. The existing helper shields an owned cleanup task and waits through repeated cancellation, while reservation transition idempotency keeps release exactly once. A new generic cleanup abstraction was rejected as unnecessary.
3. **Preserve the original cancellation if the bounded release attempt fails.** The reservation service already retries SQLite busy failures within a bounded persistence attempt. If that attempt still fails, the handler records a cancellation-neutral warning with source and model context, then uses bare `raise` so cleanup diagnostics never replace the request's `CancelledError`. The reservation remains eligible for the existing stale-reclamation backstop.
4. **Use a route-level deterministic regression.** The test starts a real source-routed `/v1/audio/transcriptions` request, waits on an upstream-entry event, cancels the request task, and inspects the exact persisted reservation and limit counter. This distinguishes direct cancellation from setup failures and does not rely on sleeps or stale reclamation.

## Risks / Trade-offs

- **Release persistence can delay cancellation propagation.** → The handler waits for the existing bounded release attempt, including SQLite busy retries, before re-raising the original cancellation.
- **Cleanup can still fail after bounded persistence retries.** → The contract distinguishes this exceptional path from successful request-owned cleanup: emit cancellation-neutral diagnostics, preserve the original cancellation, and leave the reservation eligible for stale reclamation to eventually release it and restore quota. The route-level regression verifies normal immediate cleanup; the existing stale-reservation integration regression verifies fallback quota restoration.
- **Other forwarding paths may have adjacent cancellation gaps.** → Keep this change limited to the confirmed audio seam so its ownership and proof remain reviewable.
- **Cancellation after forwarding returns enters later settlement work.** → This fix targets cancellation while upstream forwarding is in flight, matching the confirmed reproduction and regression; existing settlement semantics remain unchanged.
