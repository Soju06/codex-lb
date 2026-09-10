## Problem and decisions

On 2026-09-09, production 7-day reports took 43.7–53.3 seconds and 90-day reports returned HTTP 500 after 83.3–84.1 seconds. Exact daily speed medians were a major contributor. The options UI reused the whole reports endpoint: React Query could deduplicate the initial unfiltered key, but selecting a model or User-Agent created a separate expensive catalog request.

The existing usage rollup lacks User-Agent and the reports' source exclusion. The existing conversation satellite lacks model/API-key/User-Agent dimensions. Reusing either for filtered report totals would change the numbers. The new report history preserves all report filter dimensions and normalized conversation IDs, with sums, known-reasoning counts and first activity. There is one table, not a second copy for each timezone. UTC hours can be assembled into local days, including DST transitions.

Folded middle buckets and raw edges/live tail share one SQL snapshot. The dynamic tail bound is part of the requested_at range predicate, avoiding a historical scan merely to discard already-folded raw rows. Conversation distinct counts are computed over both segments, never by summing per-hour distinct counts. Account rekeys merge both additive measures and earliest activity under the existing fold lock.

Speed metrics remain exact for windows up to seven days, using the existing median implementation. Longer windows skip that SQL and expose `speedMetricsAvailable=false`, `speedMetricsMaxDays=7`; legacy numeric speed fields remain zero for compatibility. The UI hides those charts and explains the omission. This change does not rewrite the short-window median algorithm.

The server keeps at most 64 successful report entries and 64 options entries for 60 seconds per process. Lifespan startup initializes the shared caches before requests are accepted. Each cache serializes misses through a compute semaphore, while cache hits return immediately without waiting for unrelated slow computations. State access is synchronous on the application event loop; requests share completed work without detached tasks or shared sessions. Cancellation/failure releases ownership without caching an error. Browser queries are fresh for five minutes, do not poll or retry automatically, and provide Refresh plus the server's generation timestamp. Refresh may return the same server snapshot within its 60-second TTL.

## Validation

- Existing reports repository/service/API suite: 60 SQLite tests passed; final repository/service/cache suite: 35 tests passed.
- PostgreSQL 18: 43 report fold/API tests passed, including filter/timezone parity, pruning, lifecycle, crash/restart, concurrent folds and migration round-trip; seven focused final PostgreSQL checks also passed (cache/API, account lifecycle and exact-watermark model rewrite).
- SQLite lifecycle/retention suite: 26 tests passed. Additional report retention/migration tests: 2 passed.
- Existing hourly/conversation rollup parity and fold suites: 39 tests passed. The retention parity scenario now also folds report history and verifies that daily row membership and filtered reports survive pruning.
- Frontend: 137 reports tests passed, TypeScript check and production build passed. ESLint on changed report files passed.
- The first PR CI run exposed three App-route integration tests that still mocked options through the full reports endpoint. Updated them to handle the dedicated options endpoint and verify both queries reject inverted dates, recover with one request each, and stay idle on account-only retry. All three tests, TypeScript and changed-file ESLint passed locally after the correction.
- CodeRabbit follow-up: initialized caches during lifespan startup, allowed cached hits to bypass serialized miss computation, and guarded benchmark targets before writes. Eight SQLite cache/API tests and four PostgreSQL API tests passed, including concurrent first requests, cached hits during a blocked computation, compute limits and cancellation. Disposable PostgreSQL checks verified missing confirmation/non-benchmark names reject before connection, an empty confirmed target passes, and existing schema rejects before application DDL or inserts. Ruff, ty, architecture checks and all 64 strict specs passed.
- Ruff, ty, architecture/cancellation/timing/settings checks and strict OpenSpec validation passed.
- Playwright with mocked dashboard data: changing to 90d issued exactly one report request and one options request; long-window speed notice rendered. Screenshots are UI fixtures, not production measurements: [original error state](error-state.png), [90-day report](90-day-report.png).

## Synthetic performance evidence

PostgreSQL 18, 300,000 synthetic requests spread across 90 days, 32 MB work_mem, 256 MB shared buffers, a two-hour raw live tail. Run script: [benchmark.py](benchmark.py). Disposable DB only; set `REPORT_BENCH_DATABASE_URL` to a dedicated PostgreSQL database named `codex_lb_report_bench_*` and `REPORT_BENCH_CONFIRM_DISPOSABLE` to that exact database name. The script checks the connected database identity and absence of all user relations before DDL or inserts. Data plus indexes occupied 409 MB. No production load was generated.

| Measurement | Raw source | Report rollup + tail |
|---|---:|---:|
| Complete 90-day report, Asia/Seoul | 7.119 s | 1.622 s |
| Models/User-Agent options | 140.0 ms | 15.6 ms |
| Historical rows | 300,000 | 8,152 |

Report and options outputs were equal. Both report measurements omit long-window speed medians, so the 4.4x improvement measures aggregation changes independently of the median guard. This is one local synthetic comparison, not a production SLA. Backfill used 360 slices, 26.46 seconds total DB/application time; slowest slice 389 ms. Real compression depends on conversation/filter cardinality.

## Rollout and limits

1. Deploy the DDL migration and application/frontend together through the normal release path. Migration `20260909_060000_add_report_rollup` is based on `20260909_050000_dashboard_routing_overload_settings`; PostgreSQL upgrade/check reported one current head and no schema drift.
2. The existing leader-gated scheduler starts report backfill automatically. It processes at most eight six-hour slices per 15-minute tick, committing each slice with its watermark. The target inherits the existing two-hour fold lag. Dense 90-day history therefore takes roughly 11 hours to catch up at the default pacing; empty periods are skipped. Reads work from raw while backfill is incomplete.
3. Monitor `account_usage_rollup_state.reports_folded_through` and the `Folded report history through ...` logs. Retention waits for this watermark as well as every existing watermark. A failed slice rolls back and the next tick resumes safely.
4. Once caught up, measure 7/30/90-day authenticated reports, scoped options and DB query/I/O pressure under normal traffic. The 7-day exact median path can still be expensive; its algorithm was outside the selected work.

Do not downgrade/drop the aggregate after it becomes the only surviving copy of pruned statistics. An application rollback can leave the additive schema intact, but old code cannot mirror new report history during account mutations or protect report backfill during retention; avoid those operations in a mixed-version rollout. Backfill cannot recreate logs already pruned before this migration. Half-hour/quarter-hour timezone days use raw partial edge buckets; after those raw edges are pruned, their sub-hour contributions cannot be recovered from hourly storage. This is the same bounded edge limitation as the existing hourly statistics, and is tested for parity while raw exists.

Production was not migrated or deployed by this implementation session. Implementation started at `5794d8d7a`; rebased to main `09b48eb27` before opening the PR. The report migration was reparented to the newly merged routing-settings migration and the migration round-trip and PostgreSQL upgrade/check were rerun.
