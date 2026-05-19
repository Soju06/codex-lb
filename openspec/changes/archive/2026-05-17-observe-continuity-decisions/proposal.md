## Why

Continuity fail-closed fixes now keep clients on retryable contracts, but operators still have to infer the root cause from scattered warnings and endpoint-level error codes. That slows incident analysis for `previous_response_id` follow-ups because it is not immediately obvious whether the proxy resolved continuity from a local bridge session, request-log lookup, cache, or failed closed for a specific reason.

## What Changes

- Add structured continuity decision logs for owner resolution and fail-closed/rewrite outcomes.
- Add low-cardinality Prometheus counters for continuity owner-resolution sources and continuity fail-closed reasons.
- Cover the new observability signals with unit tests.

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: continuity-sensitive responses flows now emit explicit operator-facing diagnostics for owner resolution and fail-closed decisions.

## Impact

- Affected code: `app/modules/proxy/service.py`, `app/core/metrics/prometheus.py`, and observability-focused tests.
- Operational impact: oncall can distinguish local bridge reuse, cache/request-log owner resolution, and fail-closed continuity masking without inspecting raw upstream payloads.
