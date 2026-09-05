# HTTP timing context

See the durable [architecture investigation](../../specs/proxy-runtime-observability/context.md#responses-latency-investigation-issue-2029), [design](design.md) and normative [delta](specs/proxy-runtime-observability/spec.md). The timing boundary is the existing HTTP attempt, not client receipt and not model-only execution. The change fills existing nullable fields; historical rows are not backfilled.

For example, a request may spend time waiting for account/admission before its attempt, then wait for an upstream created event, then wait for model content. Queue, first-upstream-event, created and TTFT retain those distinct meanings. A local heartbeat does not count as an upstream event. No new telemetry framework or live service change is required.

HTTP timing depends on the accepted `publish-http-response-owner` implementation for local SSE provenance. Its `ParsedSseBlock.is_local` flag identifies generated errors, including oversized streams whose error code is outside the older transport-marker set. This distinction remains internal and does not change event bytes or native/public error envelopes. Timing checks that provenance before rewrites and retains the existing transport-marker guard.
