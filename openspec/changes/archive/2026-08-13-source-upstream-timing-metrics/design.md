## Context

OpenAI-compatible model sources already return usage through the source
forwarding layer, but their request logs leave `latency_ms` and
`latency_first_token_ms` unset. Some vLLM-compatible servers expose optional
`time_to_first_token_ms` and `generation_time_ms` fields in a top-level
`metrics` object. The dashboard and reports already derive TTFT and
generation-only tokens per second from the existing request-log fields, so the
source path can participate without a schema or frontend change.

The source response body must remain wire-compatible with the upstream. Timing
extraction is therefore observational only and must not mutate the payload or
turn malformed optional metrics into a failed proxied request.

## Goals / Non-Goals

**Goals:**

- Populate existing request-log timing fields for source-routed chat,
  responses, audio transcription, and streaming requests when the source
  provides usable metrics.
- Preserve the dashboard's existing generation-only TPS semantics across
  subscription-backed and source-routed traffic.
- Ignore absent or invalid optional metrics without changing request behavior.

**Non-Goals:**

- Adding request-log columns or changing dashboard/API schemas.
- Copying an upstream `tokens_per_second` value whose denominator may include
  TTFT and therefore differ from the dashboard definition.
- Measuring proxy network, queueing, or serialization overhead.
- Synthesizing timings when the source does not report them.

## Decisions

1. Parse only `time_to_first_token_ms` and `generation_time_ms` from the
   optional `metrics` object. These fields provide the two values needed by the
   existing request-log contract; provider-specific secondary metrics remain
   untouched.

2. Store `latency_first_token_ms = round(time_to_first_token_ms)` and
   `latency_ms = round(time_to_first_token_ms + generation_time_ms)`. The
   dashboard denominator `latency_ms - latency_first_token_ms` then resolves to
   post-first-token generation time, matching existing traffic. Copying
   upstream `tokens_per_second` was rejected because providers may include
   TTFT in that value.

3. Carry non-stream timings on the forwarding result and stream timings on the
   existing usage holder. SSE parsing captures metrics from either the final
   top-level event or the nested Responses API `response` object, alongside the
   existing usage extraction.

4. Treat timings as all-or-nothing. Missing, non-numeric, negative, `NaN`, or
   infinite values leave both log fields unset. Partial values are not recorded
   because they cannot satisfy the existing total-latency and TTFT relationship.

5. Keep timing extraction best-effort and observational. Responses are returned
   unchanged, and sources without metrics retain the previous null timing
   fields.

## Risks / Trade-offs

- [Provider timing semantics differ] -> Accept only the explicit TTFT and
  post-first-token generation fields and document the dashboard calculation.
- [Metrics are omitted from a streaming final frame] -> Leave timings null,
  matching prior behavior; do not infer proxy-side measurements.
- [Malformed optional values interrupt forwarding] -> Validate type, sign, and
  finiteness before rounding, then ignore the complete timing pair on failure.
- [Millisecond rounding slightly changes derived TPS] -> Use the same integer
  millisecond storage contract as existing request logs and cover the tolerated
  rounding in tests.

## Migration Plan

No database or API migration is required. Deploying the code starts populating
previously nullable fields for compatible source responses. Rolling back
restores the prior behavior where source timing fields remain null; existing
rows remain valid.

## Open Questions

None. Additional provider metric shapes can be proposed separately when a
stable, testable contract is available.
