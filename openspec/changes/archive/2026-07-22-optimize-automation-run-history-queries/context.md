# Automation run-history query context

## Purpose

The dashboard polls grouped automation history every 15 seconds and its filter options every 30 seconds. This change reduces repeated database work while keeping the public API and every established cycle-status rule unchanged.

## Measured baseline

The benchmark used warm-cache synthetic data representative of a busy installation: 40 jobs, 100 accounts, 10,000 cycles, 80,000 run rows, and 80,000 cycle-snapshot members.

| Query | SQLite before | Query refactor | PostgreSQL 17 before | Query refactor |
|---|---:|---:|---:|---:|
| Page, no filters | 500 ms | 74 ms | 200 ms | 96 ms |
| Page, account filter | 191 ms | 36 ms | 73 ms | 20 ms |
| Page, search | 431 ms | 102 ms | 292 ms | 73 ms |
| Options, `status=success` | 1,155 ms | not separately captured | 1,042 ms | 294 ms |

For a status-filtered PostgreSQL page, the current page statement took about 280 ms and the repeated count another 247 ms. Each traversed approximately 480,000 run rows through five `automation_runs` scan nodes. Each existing option facet repeated a graph with roughly 500,000 row visits and six run scan nodes; sort/hash work spilled to temporary storage.

These are controlled synthetic measurements, not production latency promises. They identify the repeated aggregation as the dominant cost and provide a reproducible scale target for regression tests.

## Constraints and decisions

- A candidate cycle is selected by search/model/trigger/job/account filters before whole-cycle status is derived.
- A representative run is the latest matching run by `(started_at DESC, id DESC)`.
- Manual cycle order uses the minimum manual `scheduled_for`; scheduled cycle order uses the first non-manual `started_at`.
- Model matching uses the run snapshot with the job model only as the legacy fallback.
- Grouped account filtering can match an observed run or a cycle-snapshot member.
- Effective status remains dynamic: current eligibility, `include_paused_accounts`, pending windows, hidden manual placeholders, completed/deleted outcomes, and attempt count all matter.
- An empty page beyond the final offset still reports the exact total.
- Options without a status filter evaluate account/model facets directly on matching runs. With a status filter, observed runs or snapshot membership may qualify a candidate cycle, then account/model facets expand over observed runs in the status-matching cycles; snapshot-only account IDs are not synthesized into output.

## Failed alternatives

Three natural indexes were tested: `automation_runs(cycle_key, started_at DESC, id DESC)`, `automation_run_cycle_accounts(account_id, cycle_key)`, and `automation_runs(cycle_key, account_id)`. They improved most end-to-end queries by only 0–6% on SQLite and approximately 0% on PostgreSQL, apart from about 13% on the isolated account case. At the benchmark size they consumed roughly 11 MB and would add maintenance to every automation write.

A database view does not change the plan. A materialized or denormalized summary becomes stale when time crosses a cycle window or account eligibility changes, so it requires broad dual-write, invalidation, reconciliation, migration, and backfill machinery.

## Failure modes

- A careless lightweight path can sort a cycle by its latest account attempt instead of the cycle's established manual/scheduled start.
- Applying filters to all cycle rows instead of only candidate selection can rewrite effective status.
- Loading representatives after the page query permits rows and total to come from different snapshots under concurrent scheduler writes.
- Removing the offset fallback can report total zero for a valid history page beyond its end.
- Combining option facets without a stable kind discriminator can mix account IDs and model names.

## Concrete example

Suppose a manual cycle has two attempts. A search term matches only the later attempt, while the earlier attempt supplies the cycle's original `scheduled_for`. The optimized query still chooses the later matching row as the representative, orders the cycle by the original manual start, derives status from every visible cycle member only when status filtering is requested, and returns the page row plus exact total from one statement.

## Separate follow-up

After page selection, `_enrich_runs_with_progress()` can issue several queries per unique cycle (roughly 100 additional selects for a page of 25 cycles). Batching that enrichment is valuable but is deliberately excluded so this PR remains one concern wide and reviewable.
