# Change: accurate-stream-speed-metrics

## Why

Operators report that dashboard TTFT and TPS do not match perceived model
speed. Three measurement defects compound:

1. **Anchor mismatch**: `latency_first_token_ms` is measured from the
   pre-selection request anchor (includes account selection, admission gate
   waits, and every failed failover attempt), while `latency_ms` is measured
   from the successful attempt's start. The pair is internally inconsistent;
   `latency_ms - latency_first_token_ms` (the TPS denominator) can go
   negative, silently dropping rows from the TPS median and biasing it.
   Response-create gate queueing (bridge capacity waits) amplifies the skew.
2. **Text-only first token**: first-token detection accepts only
   `response.output_text.delta` / `response.refusal.delta`. Reasoning models
   stream reasoning summary deltas long before visible text, so reasoning-heavy
   turns report multi-minute "TTFT" that is really time-to-first-visible-text.
3. **TPS numerator/denominator mismatch**: `output_tokens` includes reasoning
   tokens generated before the first text delta, but the denominator only
   spans the post-first-text window — reasoning-heavy turns report inflated
   TPS.

## What Changes

- All request-log latency timings for one request row derive from a **single
  per-attempt anchor** (the successful attempt's start). Pre-attempt time
  (selection, gate waits, prior attempts) is recorded separately as a new
  nullable `latency_queue_ms` column instead of polluting TTFT.
- First-token detection counts the first **output delta of any kind**
  (reasoning summary deltas included) so TTFT means time-to-first-model-output;
  the TPS generation window therefore covers reasoning generation and matches
  the reasoning-inclusive `output_tokens` numerator.
- Reports gain a **daily median queue-wait trend** alongside the existing TTFT
  and TPS trends, and the dashboard surfaces queue wait so operators can
  separate "load balancer wait" from "model speed".

## Impact

- Affected specs: `proxy-runtime-observability` (timing semantics + new
  queue-wait requirement), frontend behavior covered by the existing
  dashboard/report requirements' modified scenarios.
- Affected code: streaming/bridge/websocket capture paths, request-log model +
  Alembic migration (new nullable column on the current single head), reports
  SQL + schemas + API, dashboard frontend (recent-requests table, reports
  charts).
- Historical rows keep their recorded values (nullable new column, no
  backfill); the TPS median filter semantics are unchanged but stop being
  triggered by anchor skew for new rows.
