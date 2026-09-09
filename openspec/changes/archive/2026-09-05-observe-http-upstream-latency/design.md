## Context

Native HTTP passes queue and TTFT values to `_write_request_log` but omits the existing first-upstream-event and response-created fields. WS and bridge populate them. Healthy retained-WS overhead was milliseconds in the controlled full-product run; no local measurement identified the cause of historical multi-second/minute waits. The durable investigation narrative belongs to `openspec/specs/proxy-runtime-observability/context.md`.

## Goals / Non-Goals

**Goals:** Record existing nullable HTTP phase fields so pre-event delay is distinguishable from event-to-content delay, preserving current attempt/admission timing semantics.

**Non-Goals:** Change TTFT classification, time origins, client-visible timing, schema, labels, metric families, trace configuration or telemetry architecture. HTTP body preparation is a separate change.

## Decisions

- Add per-attempt nullable first-upstream-event and response-created observations inside `_stream_once`. Use the existing monotonic `attempt_started_at` after admission, the same anchor as TTFT/latency. Pre-attempt work stays in `latency_queue_ms`.
- Capture the first actual upstream data event when it enters this attempt, before downstream transformation/yield; locally generated heartbeats, comments and terminal sentinels do not invent upstream events. Capture the first observed `response.created` independently. Use explicit None checks so a legitimate0 ms observation is retained. Missing events stay null; do not manufacture created from terminal or TTFT.
- Pass these values into the existing `_write_request_log` arguments and let its existing persistence/Prometheus owner handle them. Do not add independent metrics or clocks.
- Reuse the existing `ParsedSseBlock.is_local` provenance supplied by the HTTP owner change, plus the existing synthetic-transport-failure marker, before any event rewrite. The producer keeps local origin off the wire. Marker absence alone does not establish upstream origin. This scope normally merges the accepted owner head as a dependency.
- Keep HTTP first-token and the two new phase observations in a private typed helper beside the existing streaming helpers. Move only the existing parse/classify and log-field construction into that helper to preserve the mixin size gate; later token frames still bypass parsing. Capture the first block arrival before lease-release awaits.
- Preserve first-event handling and the later lazy/verbatim SSE branch. Lifecycle events already receive typed handling; recording timestamps must not force parsing/re-serialization of token events that already take the fast path. Preserve raw bytes unless existing production transformations require rewriting them.
- Extend existing HTTP route/log timing tests with a controlled monotonic clock at the owning attempt seam and scripted upstream events. Distinct scenarios delay the first upstream event versus delay content after created. Assert exact persisted phase values and unchanged queue/TTFT origins; include missing-created and valid-zero behavior in the nearest generalized timing coverage. Reuse existing verbatim/TTFT tests and a serializer/parse spy sensitive to loss of the fast path.

## Risks / Trade-offs

- [Clock-origin drift changes reported performance] → Pin controlled timestamps across admission, first event, created and content; reuse existing attempt anchor.
- [Observability adds parsing cost] → Timestamp alongside existing classification; assert fast-path sensitivity without source-text guards.
- [Metrics mistaken for client receipt or model-only time] → Document measurement boundary and limits in context; no claim that upstream-created interval contains only model execution.

## Migration Plan

No schema migration. Newly completed HTTP rows fill existing fields; historical nulls remain null. Reverting the change restores omission without changing other timing semantics.

## Open Questions

None blocking. A local combined integration build after all accepted fixes will check overlap with owner publication and produce one wheel; it is not a new feature scope.
