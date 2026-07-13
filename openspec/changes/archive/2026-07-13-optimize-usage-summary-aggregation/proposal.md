## Why

`GET /api/usage/summary` fetched every `RequestLog` ORM row in the secondary window (typically 7 days — easily 10^5+ full rows with Text columns) and summed cost/tokens/errors in Python on every poll. The request-log write path also paid a dead `session.refresh` SELECT after every insert.

## What Changes

- Aggregate the usage-summary window in SQL (`aggregate_usage_metrics_since`): one totals query (count, errors, tokens with reasoning fallback, per-row-clamped cached tokens), the existing top-error query, and a per-model cost query. The Python summation helpers remain as the semantics oracle; new builder twins convert the aggregate into the same `UsageMetricsSummary` / `UsageCostSummary` values.
- Remove the dead `session.refresh(log)` after request-log insert (all columns set pre-insert; `expire_on_commit=False`).
- Semantics preserved and pinned by an equivalence test seeding reasoning-fallback rows, cached>input rows, negative cached, NULL-input rows, NULL-cost models, and warmup exclusions, then asserting the SQL path equals the legacy Python summation. One intentional nuance: top-error ties now resolve deterministically (count desc, code asc — the same rule the dashboard overview already uses) instead of dict insertion order.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the usage-summary window MUST be aggregated in SQL, not by hydrating window rows into Python.

## Impact

- **Code**: `app/modules/request_logs/repository.py`, `app/modules/usage/{service,builders}.py`, `app/core/usage/types.py`.
- **APIs**: response values unchanged (equivalence-tested); latency and memory drop with window size.
