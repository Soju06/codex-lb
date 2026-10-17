# Design: bound the conversation raw tail to an index range

## Problem

`conversation_presence_union` merged the folded conversation presence with its raw complement in one UNION ALL, joining the single `account_usage_rollup_state` row into both branches so the watermark, the folded segment and the raw complement came from one snapshot. The raw branch's window predicate was

```
watermark IS NULL OR requested_at < lo OR requested_at >= least(watermark, hi)
```

with `watermark` a column of the LEFT OUTER JOINed state row. The planner cannot turn an OR over a joined column (with an `IS NULL` disjunct) into an index condition, so it was applied as a per-row filter after the outer `requested_at >= since [AND < until]` range scan: every read visited the whole window although only the sub-hour edges and the un-folded tail (fold lag 2 h + fold interval) can hold raw rows the rollup does not already cover.

Measured on a scratch PostgreSQL 16 with 800k `request_logs` rows, the production indexes and the watermark at now-3h, 7-day window: Index Only Scan of 401k rows with 391k `Rows Removed by Filter`, 322 ms.

## Options

1. Resolve the watermark in a preceding statement and emit constant `RawWindow` tuples (as the hourly/demand readers do). Same plan as option 2 but relaxes the spec's one-statement/one-snapshot contract, needs a resolver, a signature change and edits to both callers.
2. Keep one statement; read the watermark as an uncorrelated scalar subquery of the state row and clamp it into `[lo, hi]`:

   ```
   requested_at < lo OR requested_at >= greatest(lo, least(coalesce(W, lo), hi))
   ```

   PostgreSQL evaluates the subquery once (InitPlan `$0`) and uses it inside the Index Cond, producing a BitmapOr of two `requested_at` ranges; SQLite plans `SCALAR SUBQUERY` + `SEARCH request_logs USING INDEX (requested_at>?)`. Same scratch corpus: BitmapOr, 9.5k rows, 31 ms. No caller changes, no signature change, the spec's mechanism sentence is the only wording that moves.

Option 2 is chosen. The folded branch bounds `bucket_epoch` by the epoch of the same scalar subquery for symmetry (a `NULL` watermark makes `bucket_epoch < NULL` false, i.e. an empty folded segment, exactly like the former inner join to a missing row).

The constant bounds are load-bearing. An OR whose arms are parameterized by a joined row (as the former reports variant had, with `lo`/`hi` coming from a CTE of per-day windows) is planned as a per-row **Filter** on PostgreSQL even with `enable_indexscan = off`: no BitmapOr path is generated for it. The dashboard union always has literal `lo`/`hi`, so the OR form is safe there.

## The labeled (reports) variant is deleted, not rewritten

`conversation_labeled_presence_union` served the reports summary and per-day conversation counts through the same joined-watermark shape. `report-aggregation` has since moved every reports read (summary, daily, per-model/account/useragent, filtered and unfiltered) onto the permanent report aggregates in `app/modules/reports/rollup_read.py`, which carry their own `reports_folded_through` watermark and already emit the raw tail as index-range bounds. That left the labeled helper with no caller; this change removes it instead of shipping a rewrite of dead code. The reports path is out of scope here.

## Degradation and snapshot

- State row missing: `coalesce(W, lo)` → tail starts at `lo`, the raw branch covers `[since, until)`; folded branch empty. Legacy raw read.
- Epoch watermark: `greatest(lo, epoch)` = `lo`; same.
- Watermark below / inside / above the window: the two ranges `[since, lo)` and `[greatest(lo, min(W, hi)), until)` are the exact complement of the folded buckets `[lo, min(W, hi))`.
- Both scalar subqueries execute inside the one statement, so the read still has a single snapshot; a fold slice committing concurrently cannot pair a new watermark with old rollup rows.

## Caveat for reviewers

The win is CPU/time. Buffer counts depend on heap clustering: on the time-scattered synthetic corpus the bitmap heap scan read more buffers (4.8k) than the former index-only scan (2k) even though it was 10x faster; production rows are inserted time-ordered, so the tail rows share few heap pages and buffers drop as well.

## Verification

`tests/integration/test_conversation_presence_union.py` pins:

- the union's aggregates against a raw-only oracle for every watermark position relative to the window (state row missing, epoch, below, inside, above; aligned, unaligned and open-ended windows; with and without display buckets and soft-deleted rows);
- the statement shape of the two switched dashboard reads (activity metrics, hour-multiple trend buckets): scalar subqueries, no state-row join, no `IS NULL` watermark disjunct, one statement each;
- on PostgreSQL, `EXPLAIN (ANALYZE, BUFFERS)` of the bind-parameter statement the application sends (a compiled `EXPLAIN` wrapper, not a `literal_binds` rendering): every `request_logs` node is an index/bitmap scan with an Index Cond on `requested_at`, runs once, removes no rows by filter, and produces exactly the oracle's raw-complement count — with the watermark missing, below the window, at the window's first folded hour (`greatest` clamp), inside, at the window end (`least` boundary), a half hour past the window end, and for a fully folded hour-aligned window (zero rows). `SET LOCAL enable_seqscan = off` is scoped to the probing transaction, so the GUC cannot leak into other tests on the pooled connection.

## Follow-ups (not in this change)

- `COUNT(DISTINCT cid)` over the union forces a sort; a `GROUP BY` form is cheaper on large unions but the remaining cost after this change is the folded satellite's 7-day cardinality, which should be measured on production first.
- No other reader in this module joins the state row into a raw branch; the hourly/demand readers already emit constant raw windows.
