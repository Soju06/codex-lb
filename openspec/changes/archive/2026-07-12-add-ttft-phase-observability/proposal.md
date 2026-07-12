# Change: Add TTFT phase observability and Codex prewarm canary

## Why

Operators can see total latency and TTFT today, but not enough phase detail to separate bridge queueing, response-create admission, upstream acceptance, first upstream event, or Codex prewarm behavior. Production evidence also shows HTTP bridge requests timing out behind old pending response-create work, after which later requests can continue queueing behind the same stalled session.

## What Changes

- Persist nullable request-log fields for low-cardinality TTFT phase timings, prewarm canary metadata, and session gap cohorts.
- Emit Prometheus histograms/counters for proxy phase latency, prewarm outcomes, and stuck HTTP bridge retirements.
- Retire an HTTP bridge session after a visible `response_create_gate_timeout` only when pending visible work is already older than the configured stuck-gate threshold.
- Split Codex prewarm behind deterministic API-key/session canary sampling, preserving the current master boolean behavior when no percent is configured.
- Add a 24-hour TTFT breakdown SQL runbook under OpenSpec context and a Grafana dashboard JSON for TTFT analysis.

## Impact

- Backward-compatible database migration with nullable columns and downgrade support.
- No raw API keys, prompt text, raw affinity keys, raw session IDs, or cache keys are logged or persisted by the new observability fields.
- Prewarm remains disabled by default. If the existing master boolean is enabled and no canary percent is configured, behavior remains equivalent to today's 100% enabled prewarm.
