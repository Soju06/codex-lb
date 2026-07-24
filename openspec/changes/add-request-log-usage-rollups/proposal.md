## Why

Dashboard time-series and statistics reads (`overview` buckets/activity/top-error, quota-planner demand bins, API-key trends) aggregate raw `request_logs` at read time by bucketing `requested_at` on the fly. At production scale (3.2M rows / 60 days) these reads take 10–37 seconds on PostgreSQL and have triggered memcg OOM. The existing usage rollups (`account_usage_rollups`, `api_key_usage_rollups`) are lifetime totals with no time axis, so they cannot serve any time-series read. Finally, when request-log retention prunes raw rows, the time-series statistics for that period vanish with them — the owner's goal is permanent statistics retention with raw logs prunable.

## What Changes

- Add three permanent time-axis rollup tables: an hourly UTC-bucketed usage rollup keyed by `(bucket_epoch, account_id, api_key_id, model, service_tier, request_kind, is_deleted)`, a lightweight hourly error-code satellite for the top-error read, and a quarter-hour demand satellite for the quota planner (the only sub-hour consumer, 900 s slots).
- Add an incremental hourly fold pass that runs inside the existing account-usage-rollup scheduler tick (same leadership gate, same fold-state row lock), advancing a new hour-aligned `hourly_folded_through` watermark on `account_usage_rollup_state`. The existing lifetime rollup watermark and rows are untouched.
- Fold slices are DELETE-then-INSERT over half-open hour-aligned windows in one transaction with the watermark advance, making folding idempotent and crash-safe, and making a watermark reset a complete self-healing rebuild.
- Switch six read paths (dashboard bucket aggregation, activity aggregates, top error, earliest activity, planner demand bins, API-key trends) to serve folded history from the rollups merged with a raw live tail above the watermark. Return shapes, services, API schemas, and the frontend are unchanged. With an epoch watermark the reads degrade to exactly the legacy raw queries, so no kill switch or setting is added.
- Mirror the request-log lifecycle mutations that rewrite folded history (account soft delete re-attribution, hard history delete, duplicate-account consolidation) into the rollup tables in the same transaction, serialized on the fold-state row lock.
- Gate request-log retention pruning on the minimum of the lifetime and hourly watermarks so raw rows are never pruned before the hourly rollup has folded them.
- One guarded, DDL-only migration (three tables plus one state column); the historical backfill is performed entirely by the paced fold job after deploy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: add the hourly time-axis rollup tables, the incremental hourly fold pass and its watermark contract, the rollup-plus-live-tail read switch for the six time-series read paths, lifecycle mirroring, and the reset escape hatch. This capability already owns the lifetime rollup + live-tail pattern and the dashboard hot-path query shapes.
- `data-retention`: request-log pruning gates on the minimum of the lifetime and hourly fold watermarks (and both currency checks), so time-series statistics survive raw pruning permanently.

## Impact

- Affected code: `app/db/models.py` (three new models, one state column), one new Alembic revision, new fold module under `app/modules/accounts/`, `app/modules/accounts/usage_rollup_scheduler.py` (call the hourly pass after the lifetime pass), `app/modules/accounts/repository.py` (delete/consolidation mirroring), `app/core/retention/job.py` (min-watermark gate), read-path repositories: `app/modules/request_logs/repository.py`, `app/modules/quota_planner/repository.py`, `app/modules/api_keys/repository.py`, plus a shared merge helper module.
- Affected tests: new fold/idempotency/lifecycle integration tests, a legacy-vs-rollup parity harness over the six switched read paths, retention gate tests, migration round-trip tests; all registered in `POSTGRES_PYTEST_TARGETS` for both backends.
- No API request/response schema change, no frontend change, no new settings or `CODEX_LB_*` env vars, no change to planner slot resolution or forecast logic.
- Non-goals (raw-only, documented): reports medians and timezone-day aggregation, conversation distinct counts, the usage-summary lifetime read switch (its inputs are folded now for a later switch), and the existing lifetime rollup semantics.
