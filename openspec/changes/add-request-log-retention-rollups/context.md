# Request-log retention context

## Purpose and scope

Raw request logs support recent debugging and continuity ownership. Daily
rollups support long-lived usage, cost, reports, and activity views after raw
detail is pruned. The seven-day window is intentionally operator-applied; this
change does not add a background scheduler or make dry-run commands destructive.

## Decisions and rationale

- Seven UTC days remain raw so recent continuity and detailed inspection stay intact.
- Apply mode compares grouped and deleted row counts before commit; mismatches roll back.
- General totals count every row because reports and API-key totals use row semantics.
- Separate account projection fields preserve the latest exact
  `(account_id, request_id, requested_at)` identity within a prune batch.
- Effective output tokens and per-request microdollars are stored separately
  because neither can be reconstructed from coarse sums after pruning.

## Constraints and edge cases

Daily rollups preserve complete UTC-day totals, not individual timestamps.
Arbitrary rolling windows can therefore be exact only for recent raw rows and
complete UTC days. Dashboard queries include complete rollup days. API-key
admission accounting is conservative: if a partial boundary day exists only as
a rollup, the entire day is counted to avoid undercounting usage. Existing
rollups are backfilled from their stored coarse totals; richer semantics are
exact for newly pruned rows.

Late duplicate rows arriving after the original identity has already been
pruned cannot be reconciled perfectly without retaining a per-request identity
ledger. The account projection preserves duplicates that coexist in the
eligible raw batch, which covers the established duplicate-row failure mode.

## Concrete example

At 2026-07-14 12:00 UTC with seven-day retention, the cutoff is
2026-07-07 00:00 UTC. July 6 rows are grouped into July 6 aggregates before
deletion; rows at or after July 7 remain raw. A 30-day dashboard combines those
July 6 rollups with recent raw rows. A monthly API limit beginning midway
through an older rolled-up day counts that whole UTC day conservatively.

## Operations and verification

Run a dry-run first, capture raw-plus-aggregate totals, create a verified
SQLite backup, then apply. After apply, require zero remaining eligible rows,
aggregate request-count growth equal to deleted raw rows, unchanged projection
totals, and `PRAGMA quick_check = ok`. File compaction is a separate operation.
