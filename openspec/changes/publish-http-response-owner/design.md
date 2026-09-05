## Context

Native HTTP `_stream_once` extracts authoritative upstream response IDs, but its final log write is detached. `_resolve_websocket_previous_response_owner` checks the existing process cache and then request logs. A real-route, two-account SQLite run produced35 failures in96 immediate follow-ups after terminal and EOF. Delaying100 ms or publishing through the existing cache produced0 failures in96 attempts. The cache experiment published at log scheduling; the production design must cover the earlier client-visible ID boundary.

## Goals / Non-Goals

**Goals:** Make an upstream response ID discoverable under the correct account and caller scope before that ID is exposed downstream on HTTP; keep log persistence detached.

**Non-Goals:** New registry/schema, all-log synchronization, transport promotion, upstream response availability guarantees, cross-replica readiness, automatic retries, or a general continuity rewrite. Current CLI0.153.2 HTTP fallback is unanchored full history; this fix is not attributed to that ordinary fallback.

## Decisions

- Call existing `_remember_websocket_previous_response_owner` from the HTTP attempt's existing authoritative lifecycle-ID extraction, before yielding the corresponding downstream event. Cover the first event and later lifecycle events; never publish the initial local request ID, a synthetic error ID, or a client-supplied previous-response ID as evidence of a new owner.
- Keep core-generated terminals distinguishable with the existing `ParsedSseBlock` carrier: `is_local` stays outside the serialized payload, survives payload reattachment, and is checked before service rewrites. This covers oversized frames and generic local errors that do not carry the existing transport-failure marker. Preserve that marker and its retry/native-boundary behavior.
- Bind the observed ID to the actually selected account, API-key ID or None, and the existing normalized session identity. Preserve the cache's bounded size and existing fallback-key semantics. Do not broaden its authorization behavior or change the durable resolver.
- The earliest standard observable ID is `response.created`. Publishing only on `response.completed`, EOF or detached persistence would leave an avoidable race. A follow-up after created must resolve the known account; the upstream still decides whether that response is usable before completion.
- Extend real Responses-route tests with two eligible synthetic accounts, actual selection/services/persistence and a scripted local upstream. Hold the originating stream before terminal after emitting created; a second HTTP request carrying that ID must dispatch on its owner without waiting for the first log. A terminal/EOF variant holds only detached log completion at its existing persistence seam. Existing scoped-owner and unknown-owner tests cover durable fallback and fail-closed behavior; extend them only where the new HTTP publication boundary is unprotected.
- Obtain red failures on pinned main at the route, then prove sensitivity by disabling publication. Keep the production path from upstream boundary through response delivery and owner selection intact. Caller cancellation must not create a false owner or leak tasks; a valid already-observed owner need not be deleted when the client disconnects.

## Risks / Trade-offs

- [A synthetic/local ID accidentally becomes ownership evidence] → Publish only an ID extracted from an actual parsed upstream lifecycle event.
- [Scope leakage] → Use the existing API-key/session normalization and bounded owner method; retain existing adversarial scoping tests.
- [Concurrent follow-up reaches an unfinished upstream response] → Guarantee account resolution only, not upstream storage readiness; preserve upstream error semantics.
- [Another replica lacks the cache] → Durable cache-miss lookup and unknown-owner fail-closed behavior remain; no new cross-worker promise.

## Migration Plan

No migration. Cache publication starts for new observed responses; persisted rows remain the fallback. Rollback removes only the early in-process publication behavior.

## Open Questions

None blocking. HTTP timing depends on this change's producer provenance and will normally merge the completed owner head before its timing commit. The shared correction is owned here; the combined build must run both route regressions.
