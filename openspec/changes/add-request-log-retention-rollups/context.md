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

## Production verification on 2026-07-14

Railway deployment `a84956b8-0c0b-4a7a-b750-805b95ffb802` ran the
seven-day change against the production SQLite volume. A dry-run selected
54,551 raw rows before 2026-07-07 00:00 UTC and 2,740 aggregate groups. Apply
wrote all 2,740 groups and deleted exactly 54,551 rows; the immediate repeated
dry-run selected zero rows.

Fixed-cutoff API-key, account-deduplicated, and general dashboard/report
projections were compared before and after apply. All API-key and general
checksums matched. The account repository projection covered 84 accounts and
had identical before/after checksums with zero field differences. SQLite
`quick_check` returned `ok` before apply, after apply, and after compaction.

The pre-prune backup is
`/var/lib/codex-lb/store.pre-prune-20260714T012557Z.db` with SHA-256
`f6f0d2d3a508f9dab3a8ab6556d031fc12e0bbafc948394aa8c2995367089d61`.
Its gzip copy has SHA-256
`20e82369baf16ec733cb56d46b977566bf981928b86daaf9a24129af77382011`.
Compaction reduced `store.db` from 1,292,894,208 bytes before the snapshot to
1,059,004,416 bytes after live traffic resumed. Health checks and proxied
Responses API requests continued to return HTTP 200.
