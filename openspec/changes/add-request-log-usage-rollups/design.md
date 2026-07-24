## Context

Reference commit: `origin/main` 3e9e36aa. Verified surfaces: `app/modules/accounts/usage_rollup.py` (lifetime fold), `usage_rollup_scheduler.py`, `app/core/retention/job.py`, `AccountsRepository.delete()`/consolidation, `app/modules/request_logs/repository.py`, `app/modules/quota_planner/{repository,logic}.py`, `app/modules/api_keys/repository.py`, `app/modules/dashboard/service.py`, `app/db/models.py`, Makefile `POSTGRES_PYTEST_TARGETS`, Alembic single head `20260722_000000_backfill_request_log_useragent_families`.

Time-series reads currently bucket raw `request_logs` at read time (`_bucket_epoch_expr`, planner `slot_epoch = floor(epoch/900)`). Production: 3.2M rows / 60 days, ~14 accounts, ~15 API keys, ~10 models; observed 10–37 s reads and PostgreSQL memcg OOM. The lifetime rollups have no time axis; retention (live since 20260716, dashboard-enable-able) deletes the only source of time-series history.

Hard constraints inherited from the owner: raw traffic stays permanently *statistically* represented even after raw pruning; folded buckets are never recomputed from raw that may no longer exist; reads must be exactly equal to the legacy raw reads while raw is present (parity harness is the enforcement mechanism).

## Goals / Non-Goals

**Goals:**

- Serve dashboard overview buckets/activity/top-error, planner demand bins, and API-key trends from permanent hourly (planner: quarter-hour) rollups plus a bounded raw live tail.
- Incremental, idempotent, crash-safe, leader-gated fold with a paced initial backfill of the full history.
- Statistics survive request-log pruning; retention never prunes rows the hourly fold has not covered.
- Zero new settings, zero API/frontend changes, automatic legacy-equivalent degradation when the rollup is empty.

**Non-Goals:**

- Reports medians (TTFT/TPS/queue percentile-family metrics are not additive) and arbitrary-IANA-timezone day aggregation: raw-only, available for the raw retention window.
- Conversation distinct counts (`COUNT(DISTINCT …)` is not additive): raw-only, same caveat.
- Switching the lifetime usage-summary read (`aggregate_usage_metrics_since`): its required measures (`cached` clamp, cost row count, output-or-reasoning) are folded now so a later change can switch it without a one-way-door loss.
- Lifetime account/API-key rollups: unchanged, including their dedupe semantics (which the hourly rollup intentionally does not share — the switched read paths do not dedupe today either).
- Planner slot resolution (900 s), forecast logic, retention policy semantics, hourly-to-daily coarsening, and any new env/dashboard setting.
- `/api/accounts/{id}/trends` (reads `usage_history`, not `request_logs`).

## Decisions

Numbered judgments (D1–D11) resolving the design alternatives that were considered.

### D1: Separate `hourly_folded_through` watermark column (not shared/reset `folded_through`)

Retention may already have pruned raw history on live installs; any plan that resets the lifetime watermark and re-folds would permanently lose lifetime totals. A separate column leaves the lifetime rollup untouched and avoids any post-deploy full-scan regression window for lifetime summaries. It lives on the same `account_usage_rollup_state` id=1 row, so the existing `FOR UPDATE` row lock serializes all folds, mirrors, and consolidation with no new locking primitive.

### D2: DELETE-then-INSERT slices with an hour-aligned half-open watermark

Each fold slice, in one transaction: defensively DELETE rollup rows in `[start, slice_end)`, INSERT..SELECT the recomputed groups, advance the watermark to `slice_end`, commit. Crash = full rollback = clean re-run. A rewound watermark (escape hatch) re-converges unconditionally — no double counting (add-fold's failure mode) and no stale leftover rows (replace-upsert's failure mode). The hour-aligned watermark with `[inclusive, exclusive)` windows removes the boundary epsilons and open/closed-interval mismatches of the lifetime fold's `(exclusive, inclusive]` convention; the two folds have different boundary contracts and therefore stay in separate functions (D3).

### D3: Separate `run_hourly_fold_pass`, same scheduler tick

Called by `AccountUsageRollupScheduler._fold_once` immediately after the lifetime `run_fold_pass()` — zero new scheduler/leadership wiring, covered by the existing scheduler-coordination requirement for the account usage-rollup scheduler. Separate pass function because the watermark and boundary conventions differ (D1/D2) and to isolate blast radius: an hourly aggregation bug cannot stall the lifetime fold; retention fails safe (stops pruning) via the min gate rather than corrupting.

### D4: Dimensions — six on the main table, error codes in a satellite, nothing speculative

Measured consumers of the switched read paths: bucket aggregation uses model+service_tier (+kind filter, no deleted filter), activity uses no dimension, trends uses api_key_id, planner uses slot+kind (+deleted filter). Decisions:

- `status` is consumed by no switched path as a dimension → folded as the `error_count` measure.
- `error_code` has unbounded cardinality → excluded from the main table; a minimal `(bucket_epoch, account_id, error_code)` satellite serves the top-error overview hot path. `account_id` is on the satellite solely so hard-delete can mirror.
- `account_id` is kept on the main table even though no switched path filters by it: it is required for lifecycle mirroring, and dimensions are a one-way door once raw is pruned (co-occurring dimension, negligible cardinality cost).
- `request_kind` is `NOT NULL server_default 'normal'` on `request_logs` (verified `models.py:263`) — stored as-is, no sentinel needed. Only `account_id`/`api_key_id`/`service_tier` are nullable → `''` sentinel so they can join the PK on both dialects (UNIQUE/PK NULL-distinctness differs across backends).
- `source`, `useragent_group`: zero switched-path consumers → not folded (recorded here as an explicit rejection; reports are a non-goal). `reasoning_effort` is likewise absent from the hourly table, but IS a quarter-demand dimension (see D5: the planner grain is load-bearing).
- No dedupe (#904 duplicates counted): every switched path is no-dedupe today; dedupe remains a lifetime-rollup-only semantic.

Estimated main-table growth: tens to low hundreds of realized combinations per hour → 50k–200k rows / 60 d (2–6% of raw), fine to keep forever. Pre-implementation gate: measure production distinct hourly combinations once; if >1,000/hour, demote `service_tier` to a satellite (tracked in tasks).

### D5: Planner demand satellite carries the FULL legacy grain + `is_deleted`

`AccountsRepository.delete()` soft delete retroactively rewrites the account's entire `request_logs` history (`account_id=NULL, deleted_at=now`), and the planner filters `deleted_at IS NULL`. If the fold dropped deleted rows at fold time, an account deletion could never be corrected in folded slots and the planner would diverge from raw permanently. With `account_id` and `is_deleted` as dimensions, deletion mirrors as a bucket-wise move to `(account_id='', is_deleted=true)`.

The satellite additionally keeps every legacy `GROUP BY` dimension (`api_key_id`, `model`, `reasoning_effort`, `status`) even though `DemandBinLike` consumers never read those fields: the grain itself is load-bearing. `_bin_demand_units` computes `max(token_units, cost_units, request_units)` PER BIN before summing, a nonlinear step — folding to a coarser grain merges bins with different dominant components and systematically shrinks folded-history forecasts relative to raw (caught in review). Row count equals what the legacy runtime `GROUP BY` materialized per query, so cardinality is bounded by realized traffic combinations per 900 s slot.

### D6: `BIGINT` epoch bucket keys

All switched readers already operate in integer epoch arithmetic (`_bucket_epoch_expr`, planner `slot_epoch`, trends `bucket_epoch`). Regrouping to display buckets (3600/21600/86400 s — all multiples of 3600, epoch-aligned) is identical arithmetic on both dialects; timestamp keys would reintroduce per-dialect string/date round-trips. The watermark itself stays a `DateTime` state column (invariant: value is hour-aligned).

### D7: Read switch = single-statement watermark+rollup read, raw tail, Python merge — one shared helper

One merge primitive implemented once (a new small module, not inline per repository — three hand-rolled copies is how drift starts):

1. `account_usage_rollup_state` LEFT JOIN rollup in **one statement** so the watermark W and the folded sums come from one snapshot (READ COMMITTED safety, same contract as the lifetime read).
2. Raw tail aggregated with `requested_at >= W` plus the path's existing filters. W is hour-aligned, so folded (`< W`) and tail (`>= W`) partition the data exactly, and W can never split a display bucket.
3. Python dict-add merge; non-aligned `since` uses three segments (raw head `[since, ceil_hour(since))`, rollup `[ceil_hour(since), W)`, raw tail `[W, until)`). The raw head over already-pruned ancient history is a documented, deterministic ≤1 h undercount outside parity scope.
4. W = epoch (pre-backfill or after reset) → the folded segment is empty and every read equals the legacy query. This is the kill switch: none is needed (D9).

Switched functions (all repository-internal; dataclass shapes, services, API schemas, frontend unchanged): `request_logs.aggregate_by_bucket`, `aggregate_activity_since/_between` (conversation distinct counts split into a separate raw statement — documented statement-count change), `top_error_since/_between` (satellite + tail, tie-break count desc then error_code asc reproduced in Python), `earliest_activity_at` (raw first, `min(bucket_epoch)` hour-precision fallback when raw is pruned — no dedicated columns; sole consumer is a boolean comparison), `quota_planner.aggregate_demand_bins` (quarter satellite `is_deleted = false` + tail; folded bins reconstruct the full legacy grain from the satellite dimensions — see D5), `api_keys.trends_by_key` (hourly granularity native, output from `output_or_reasoning_tokens`).

### D8: Measures include forward-looking columns now (one-way door)

`output_or_reasoning_tokens` (sum of coalesce — not derivable from separate sums), `cached_input_tokens_clamped` (per-row clamp), and `cost_count` (rows with non-NULL cost, for the "all-NULL model excluded from cost" rule) are folded from day one even though only later switches consume some of them: they cannot be backfilled after raw is pruned.

### D9: No kill switch, escape hatch instead

Incident recovery = one transaction: DELETE all rows of the three rollup tables + reset `hourly_folded_through` to epoch. Reads instantly degrade to legacy-equivalent, the next fold pass rebuilds from surviving raw, and retention auto-pauses via the min gate. Running only half of the escape hatch is forbidden (documented invariant). No new env var or dashboard setting (simplicity gate: zero-config default is the only config).

### D10: Lifecycle mirroring, serialized on the fold-state lock

The only code paths that legally rewrite raw below the watermark (verified exhaustively):

- `AccountsRepository.delete()` — **must acquire `lock_fold_state()` first** (it does not today; without it a fold slice could snapshot pre-reattribution rows and commit after the delete). Soft path: merge-add account A's rows into `(account_id='', is_deleted=true)` keys across all three tables, then delete A's rows — the exact mirror of the raw UPDATE. Hard path (`delete_history=True`): delete A's rollup rows.
- Duplicate-account consolidation (`merge_rollups_into` transaction): bucket-wise merge-add dup→canonical, delete dup rows, same transaction.
- `update_model_for_request` (post-hoc model/cost rewrite): the intended target — the row the caller inserted moments earlier — is always absorbed by the 24 h `FOLD_LAG`. But the rewrite matches by client-controlled `request_id`, so a reused id can collide with unrelated pre-watermark history (caught in review). It therefore takes the fold-state lock and bounds the rewrite to `requested_at >= max(lifetime, hourly watermark)` — a row is un-folded by EVERY rollup only above the most advanced watermark (min() is the retention gate's direction, not the rewrite gate's): folded rows are skipped rather than mirrored, which is also the correct semantic (an old collision row was never this request).

Discipline (spec MUST + module docstring): any new code that mutates a folded dimension or aggregated column of request-log rows below the hourly watermark must take the fold-state lock and either mirror the rollups or exclude the pre-watermark rows from the mutation.

### D11: Backfill pacing

Constants (code, not settings): slice = 48 h, ≤20 slices per pass, `FOLD_LAG` = 24 h inherited (absorbs stream-end insert latency and post-hoc model/cost rewrites). 60 d of history backfills in ~2 scheduler ticks (~30 min) while bounding I/O bursts and fold-state lock hold time (PostgreSQL memcg OOM history motivates the cap). First tick runs immediately on leader start. Pre-scan `min(requested_at)` (unconditional — hourly folds all rows including warmup/deleted/orphaned) jumps the start over empty history.

## Risks / Trade-offs

- [Risk] Unfolded dimensions are unrecoverable once retention prunes raw (one-way door). → D4 keeps every dimension any switched path consumes plus `account_id`; owner sign-off required before enabling retention; rejections recorded here.
- [Risk] A late soft delete (after `FOLD_LAG`) or any future history-rewriting path outside D10 silently diverges rollups from raw. → The D10 discipline is a spec MUST; parity tests include the lifecycle mutations.
- [Risk] Hourly combination cardinality exceeds estimate. → Pre-implementation production measurement gate with a `service_tier` satellite demotion fallback (D4).
- [Risk] NULL→`''` sentinel mapping mismatch between fold and readers is a silent divergence. → Parity harness seeds NULL `account_id`/`api_key_id`/`service_tier` rows explicitly; mandatory review item.
- [Risk] Retention pruning deployed without the hourly gate would permanently hole the time series. → The min-watermark gate ships in the same PR as the schema and fold (atomic; splitting the PR at that seam is rejected in advance).
- [Risk] Fold and reader race under READ COMMITTED. → Watermark+rollup in one statement (D7), fold serialization via the existing row lock, dedicated interleaving tests.

## Migration Plan

One revision `20260724_000000_add_request_usage_time_rollups` (parent: current single head `20260722_000000_backfill_request_log_useragent_families`; re-verify `alembic heads` before authoring). DDL only, no data manipulation, non-blocking at boot: guarded `CREATE TABLE` ×3 (`inspector.has_table`), guarded `ADD COLUMN hourly_folded_through` NOT NULL server_default epoch (`get_columns`; SQLite via `batch_alter_table`). `folded_through` and lifetime rollup rows untouched. Downgrade: guarded drops. Models declared in `app/db/models.py` in the same commit (PostgreSQL drift contract covers them; no partial/expression indexes, so no manual drift registration). No secondary indexes — every consumer leads with a bucket range on the PK; add only on post-deploy EXPLAIN evidence.

Initial backfill is the fold job itself (D11). Runbook line: after deploy, confirm `hourly_folded_through` advances to ~now−24 h.

Rollback: revert the code; the escape hatch (D9) clears rollup state if needed; reads and retention degrade to legacy behavior automatically.

Delivery: PR1 = this change + migration + models + fold + lifecycle mirroring + retention min gate + fold/idempotency/retention tests. PR2 = the six read switches + parity harness + performance evidence. Conventional Commits; ruff/ty/architecture-ratchet clean (merge helper is a new module, keeping `request_logs/repository.py` under its line gate).

## Open Questions

- Production hourly distinct-combination measurement (D4 gate) — to be captured before implementation starts; determines whether `service_tier` stays on the main table.
- Owner decision, later change: approximate percentiles (histogram sketches) for reports medians, if permanent reports history is ever wanted.
