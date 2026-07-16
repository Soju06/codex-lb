# Tasks: accurate-stream-speed-metrics

## 1. Schema

- [x] 1.1 Alembic migration adding nullable `request_logs.latency_queue_ms`
      (Integer) on head `20260713_040000_add_account_refresh_claims`, with
      column-presence guards and downgrade
- [x] 1.2 `RequestLog.latency_queue_ms` model column; repository `add_log`
      param; `_write_request_log`/`_persist_request_log` plumbing; request-log
      mappers/schemas expose it

## 2. Capture

- [x] 2.1 HTTP streaming (`streaming/mixin.py`): capture
      `latency_first_token_ms` from the per-attempt `start` (same anchor as
      `latency_ms`) and record `latency_queue_ms = start - request_started_at`
- [x] 2.2 First-token event set: reasoning deltas
      (`response.reasoning_summary_text.delta`, `response.reasoning_text.delta`)
      count for TTFT capture at all four capture sites (streaming mixin ×2,
      websocket mixin, bridge streaming) without changing the text-delta
      semantics used elsewhere (saw_text_delta, done suppression, retry gates)

## 3. Reports & dashboard

- [x] 3.1 `_daily_speed_medians_stmt`: add queue-wait median CTE;
      `DailyReportAggregateRow.median_queue_ms`; service rounding;
      `DailyReportRow.median_queue_ms`
- [x] 3.2 Frontend: `medianQueueMs`/`latencyQueueMs` schema fields, queue-wait
      trend chart on Reports, queue wait in the request-log details dialog

## 4. Tests

- [x] 4.1 Unit: failover scenario — TTFT excludes pre-attempt time and
      `latency_queue_ms` captures it; reasoning-delta TTFT capture; ws paths
      log `latency_queue_ms=None`
- [x] 4.2 Unit: reports queue median (odd/even/missing samples, zero-fill),
      service rounding; request-log repository persists the new column
- [x] 4.3 Migration upgrade/downgrade coverage where the project expects it
- [x] 4.4 Frontend: chart + details dialog tests

## 5. Validation

- [x] 5.1 `openspec validate accurate-stream-speed-metrics --strict`
- [x] 5.2 Full affected suites + ruff/ty/architecture cap
