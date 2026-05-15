# Proxy Runtime Observability Context

## Purpose and Scope

This capability defines what operators should be able to see while debugging proxy traffic: live server console logs, request-log summaries, and Prometheus metrics for sustained deployments.

See `openspec/specs/proxy-runtime-observability/spec.md` for normative requirements.

## Decisions

- **Timestamps are always on:** timestamped console logs are a baseline operator need, not a debug-only feature.
- **Request tracing is opt-in:** outbound request summary and payload tracing remain configurable because payload logs can be noisy or sensitive.
- **Error logs must be correlated:** request id, endpoint, status, code, and message are the minimum useful fields for debugging 4xx/5xx failures.
- **Metrics stay low-cardinality:** Prometheus labels may include bounded values such as route shape, status, transport, error class, bridge outcome, SQLite retry outcome, and service tier. They must not include prompts, bearer tokens, raw request ids, response ids, or auth headers.
- **Service-tier verification is observational:** codex-lb preserves the requested tier and records the upstream actual tier when upstream reports one. A mismatch is visible through request-log snapshots and the service-tier mismatch counter; it does not require changing the operator's tier preference.

## Operational Notes

- Use request ids to correlate inbound proxy logs, outbound upstream traces, and client-visible failures.
- Prefer summary tracing in normal debugging sessions; enable payload tracing only when the exact normalized outbound request matters.
- For direct compact `5xx` failures, look for `proxy_compact_failure` alongside `upstream_request_complete`; together they show the compact failure phase, failure detail, exception type, retry metadata, and affinity source.
- For the greenfield sustained baseline, enable `CODEX_LB_METRICS_ENABLED=true` and scrape the metrics port. The minimum alert bundle covers readiness, request success rate, p95 latency, upstream timeout or `stream_incomplete` spikes, SQLite lock pressure, bridge continuity errors, service-tier mismatches, and container restart/OOM signals.
- For service-tier verification, capture a snapshot with `scripts/codex_lb_live_snapshot.py` and inspect `request_logs.service_tier_counts` plus `request_logs.tier_mismatches`. The snapshot summarizes requested and actual tiers without printing prompts or auth headers.
