## Diagnosis

On September 9, 2026, a normal local dashboard load returned request rows in 20 ms, overview in 674 ms, and projections in 118 ms. `GET /api/request-logs/options` took 4,738 ms; a separate request took 6,194 ms. Docker CPU rose to roughly one core during the latter request. Cleanup had completed, and the delay reproduced afterward.

The deployed revision was `5fd8b1053489b04b5dfeca9e125a6a9c93b7e5f8`. The SQLite database had no `sqlite_stat1` or `sqlite_stat4`. Read-only query plans selected `idx_logs_deleted_at_requested_at_id (deleted_at=?)` for both the seed and successor of account, model, and API-key facets. Those probes repeatedly read the live cohort.

A read-only prototype on the same database separated facet traversal from visibility. The complete four-facet SQL sequence finished in 10.8 ms. This is SQL-level evidence, not a measured deployment of the complete endpoint.

The request-log options implementation is unchanged between the deployed revision and the pinned upstream base `6658e19c50534f0b5394a9d929abae9e5020d2d2`. [PR #2246](https://github.com/Soju06/codex-lb/pull/2246) adds live-row indexes for another facet-query problem. It is complementary: adding its indexes to the synthetic SQLite corpus did not repair the original traversal plan without statistics.

## Example

For account IDs `a` and `b`, the old seed asks SQLite to find `min(account_id)` among all visible rows. The new SQLite traversal reads the first indexed account ID, then checks whether an eligible row exists for that ID. Its successor seeks `account_id > previous` in the account index. Repeating the same visible rows no longer repeats a whole-cohort minimum scan.

For a model/reasoning pair, enumerate reasoning values only inside the current model's index prefix. Check visibility for the complete pair so a reasoning value from a deleted row cannot leak into the returned options.

## Verification boundary

Live probes use read-only SQLite connections and return only plans, durations, and result counts. Regression data is synthetic in a dedicated temporary database. No production database writes, service restart, or deployment are authorized by this change.

The endpoint regression uses 20,006 synthetic rows and counts SQLite VM operations across the actual request connections. The original upstream implementation executed 1,717,000 operations without the proposed live-row indexes and 1,681,200 with them. The revised query executes 1,200 and 1,000 respectively, returning identical expected JSON, including empty and NULL reasoning values while excluding deleted-only and unsupported-status values. This count comparison is deterministic work evidence, not a claim that endpoint wall time improves by the same factor.

The targeted suite passed all 15 tests. Repository lint, architecture/cancellation/timing/settings checks, type checking, and strict validation of the change and all 64 main specs passed. Full hosted CI and deployment remain separate gates.
