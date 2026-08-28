# Context: measurements behind the 2h fold lag

All numbers from the reference production deployment (PostgreSQL 18,
2 vCPU), 2026-08-16, read-only.

## Insert-visibility skew (what the lag must actually cover)

`requested_at` is assigned inside `RequestLogsRepository.add_log` as
`requested_at or utcnow()`; a repo-wide audit found **no live caller passing
an explicit value** (the parameter exists for tests; the
`limit_warmup` Protocol declares it but its call site does not use it). The
log write happens at stream end and is dated at the write, so the
stream-start-dating threat the original 24h comment guarded against does not
exist — and has not existed since the initial commit.

Frontier-lag measurement (how far below the running `max(requested_at)` by
insert order — `id` — a row lands at insert):

| window | rows | p99 | p99.9 | max |
|---|---|---|---|---|
| last 2M rows (~3.6 days) | 1,999,999 | 4.6ms | 46ms | 5.36s |
| full history | 5,992,511 | — | 30ms | **7.89s** |

This bound covers every insert path (normal, warmup, duplicates), because it
is computed over every row. 2h ≈ 900x the all-history worst case, with room
for replica clock skew, paused VMs, and event-loop stalls far beyond
anything observed.

Operational bound made explicit by this change: writer-replica wall clocks
(the `utcnow()` used by `add_log`) must stay within one fold lag of the fold
leader's clock, and no insert transaction may stay open that long. This
requirement is not new — it existed at 24h and only its margin changed. All
replicas share one database and one NTP discipline; a clock trailing by two
hours implies TLS/OAuth breakage long before rollup drift, and the DB pool
recycles connections far below the lag. The residual exposure (a 2–24h
trailing clock that the old lag absorbed) is accepted; deployments that
cannot bound clock skew should not shorten further. A hard fence (stamping
`requested_at` from the database clock) would cost the write path's
no-refresh optimization and is left as a follow-up if ever needed.

Post-insert mutators are fenced independently of the lag:

- `update_model_for_request` selects only rows strictly above the lifetime
  watermark and at/above the hourly watermark, under the fold-state lock.
- Account deletion and duplicate-identity consolidation reassign request
  logs under the fold-state lock and mirror folded sums
  (`merge_rollups_into`, time-rollup mirrors).

## Cache invalidation race (generation fence)

A summary fill that is between its two statements when deletion or
consolidation commits could otherwise store its pre-commit result *after*
the lifecycle clear, serving stale attribution for a full TTL. Fills capture
a generation counter before their first await; `_clear_...` bumps it and
stores are discarded on mismatch. The clear runs synchronously right after
the lifecycle commit (no await between them), so on the single event loop
every store either precedes the commit (wiped by the clear) or observes the
bumped generation. Regression:
`test_summary_cache_fill_discarded_when_invalidated_mid_flight`.

## Read-path cost of the 24h tail

- Tail at measurement time: 655,804 of 5,992,447 rows (11%).
- Listing aggregate (`deduped_usage_aggregate_stmt` above the watermark),
  production `EXPLAIN (ANALYZE, BUFFERS)`:
  - **24h watermark (actual)**: 59.96s cold — the planner abandons the
    covering index at ~656k tail rows and degrades to a parallel seq scan
    (external-merge sort, 28MB spill) hash-joined against a hash of the
    entire 5.99M-row table (46,474kB hash memory, 16 batches, 75k temp
    pages). Warm-cache production average for the same call family: 2.1s
    over 1,459 calls, max 131s.
  - **2h parameter, same query shape**: 63ms — index scan on
    `idx_logs_dash_usage_covering` (9,124 rows), hash-agg dedupe, nested
    loop over `request_logs_pkey`.
  - **1h parameter**: 18ms.
- No index or query-shape change is needed once the tail is bounded; the
  existing covering index already serves the bounded range. The residual
  per-render cost is then amortized by the 30s summary cache.

## Upgrade path

`run_fold_pass` folds toward `now - FOLD_LAG` in bounded `FOLD_SLICE` (7d)
transactions, so the one-time 22h watermark jump after this change lands in
a single ordinary slice; `test_fold_absorbs_widened_watermark_gap` pins the
totals across the jump. The watermark only advances, so a rollback to the
24h constant simply pauses folding until `now - 24h` catches up with the
already-advanced watermark — reads stay correct throughout (rows above the
watermark are always live-tail-served).

## Retention interaction

The retention job's raw-prune floor (`watermark - FOLD_LAG`) and freshness
gate (`watermark` within `2 * FOLD_LAG` of now) tighten with the constant.
The floor exists so no rollup is robbed of raw it has not folded and so
concurrent readers holding a slightly older watermark lose nothing; both
need seconds. Consequence for retention-enabled deployments: raw becomes
physically prunable at ~4h age instead of ~48h when the configured retention
period is that short; sub-hour partial-window reads (non-hour-aligned
`since`/`until`) over pruned history hit their documented raw-degrade path
sooner. Hour-aligned reads are rollup-served and unaffected.

The rollup/retention parity corpus
(`tests/integration/test_request_usage_rollup_parity.py`) was authored
against the 24h lag (TARGET_W at BASE+9d, prune floor at BASE+8d, unaligned
windows and boundary rows placed between them); it now pins
`CORPUS_FOLD_LAG = 24h` explicitly because the parity semantics it proves
are lag-independent.
