# Bound the account-summary live tail: 2h fold lag + short summary TTL cache

## Why

The account listing recomputes its request-usage summaries by deduping and
re-aggregating every raw `request_logs` row above the lifetime fold watermark
on each dashboard accounts load. The watermark trails `now` by the fold lag,
so the lag directly sizes that always-rescanned tail.

The lag has been 24h since the rollup shipped, justified by a premise the
write path never had: the sizing comment (and the fold spec scenario) claim a
log row is *dated at request start but inserted at stream end*, so the lag
must exceed the maximum request duration. In this codebase `requested_at` is
stamped **inside `RequestLogsRepository.add_log` at write time**
(`requested_at or utcnow()`, and no live caller passes an explicit value —
the parameter exists for tests). A row can land below the `requested_at`
frontier only through replica clock skew, the single-row insert transaction's
commit latency, or a process stall. Measured over one full production history
(6.0M rows), the worst insert landed 7.9s below the frontier (p99.9 = 30ms).
Post-insert mutators need no lag allowance either: `update_model_for_request`
skips rows at/below the watermarks, and account consolidation/deletion
reassign logs under the fold-state lock while mirroring the folded sums.

The oversized lag is expensive: on the reference deployment the 24h tail is
~660k rows (11% of the table), the listing aggregate was measured at 1,459
calls averaging 2.1s (max 131s), and the cold plan degrades to a full
seq-scan hash join over the whole table (measured 60s). With a 2h bound the
identical query runs in 63ms via the covering-index nested-loop plan.

Independently, the listing recomputes the summaries on every accounts load
even though the displayed lifetime totals tolerate short staleness — the same
shape the request-log COUNT cache already addresses (issue #1340).

## What Changes

- `FOLD_LAG` drops from 24h to 2h. 2h keeps a ~900x margin over the worst
  insert-visibility skew ever observed while bounding the raw tail every
  account and API-key summary read must re-aggregate. The hourly and
  conversation fold targets and the retention min-gate derive from the same
  constant and tighten with it; the retention floor's purpose (protect raw
  the folds have not consumed and concurrent readers holding a slightly
  older watermark) needs seconds, not hours.
- On upgrade, the first fold pass absorbs the 22h watermark jump as ordinary
  bounded backfill slices; totals are unchanged (fold moves rows from the
  live tail into the persisted sums).
- The fold-lag spec scenario is corrected to state the real invariant
  (insert-visibility skew), replacing the false stream-start-dating premise.
- Account request-usage summaries gain a process-local fixed-TTL (30s) cache
  keyed by the account-id signature, mirroring the request-log COUNT cache.
  Account deletion and duplicate-identity consolidation clear it because they
  re-attribute usage rather than merely append; a non-positive TTL bypasses
  the cache (the test suite runs with TTL 0).

No schema change, no new settings, no API change.

## Impact

- Affected specs: `query-caching` (fold safety-lag requirement, account
  summary read requirement).
- Affected code: `app/modules/accounts/usage_rollup.py` (constant + sizing
  rationale), `app/modules/accounts/repository.py` (summary TTL cache +
  invalidation hooks), `tests/`.
- Behavior visible to operators: account/API-key summary reads get a 2h raw
  tail instead of 24h; listing summaries may be up to 30s stale (matching the
  dashboard's 30s poll cadence); with retention enabled, raw rows become
  physically prunable once they are one fold lag (now 2h) below the
  watermark, so sub-hour partial-window raw reads over pruned history reach
  their documented degrade path sooner.
