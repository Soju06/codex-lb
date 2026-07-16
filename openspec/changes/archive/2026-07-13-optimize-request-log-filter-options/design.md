## Context

The options endpoint feeds the request-log filter panel. `list_filter_options` builds always-on conditions (`deleted_at IS NULL`) plus optional user filters, then runs four `DISTINCT` selects. PostgreSQL cannot skip-scan a btree for `DISTINCT`, so with no user filters each select reads the entire index (`idx_logs_account_id_requested_at`, `idx_logs_model_reasoning_requested_at`, `idx_logs_api_key_requested_at`, `idx_logs_status_error_requested_at`). Facet cardinalities are tiny (tens), table cardinality is unbounded.

## Goals / Non-Goals

**Goals:** unfiltered options load in O(distinct values × log n); identical results and ordering; both backends.

**Non-Goals:** changing filtered-request query shapes (bounded by the user's filters); caching; dimension tables (write-path cost and drift risk are not justified at these cardinalities).

## Decisions

### D1. Recursive-CTE loose index scan, gated to unfiltered requests

The classic emulation: seed with `min(col)` under the facet conditions, then repeatedly select `min(col) WHERE col > previous`; every step is one btree probe on the existing facet index. Chosen over:

- *Default time window*: changes visible option sets (older accounts/models vanish) — a behavior change the compatibility rules disallow without need.
- *Dimension tables maintained at insert*: adds hot-path write cost and a drift/backfill liability for four dropdowns.
- *Applying skip scan to filtered requests too*: with selective residual filters (e.g. one account), each probe may scan far into a facet block to find a matching row — asymptotically worse than the bounded `DISTINCT` it replaces. Filtered requests keep the current shape; the gate is "no user-supplied filters", the exact case that is pathological today.

### D2. Pair facets via nested skip scan plus NULL-presence probe

`(model, reasoning_effort)` and `(status, error_code)` iterate the leading column by skip scan; for each value, the second column's distinct non-NULL values come from a nested skip scan under `leading = value`, and a `LIMIT 1` probe detects a `(value, NULL)` pair. All probes are prefix matches on the existing composite indexes. Portable lexicographic row-value stepping over a nullable second column was rejected: SQLite orders NULL first, PostgreSQL last, making one CTE per pair facet dialect-divergent and error-prone. NULL pairs are emitted before non-NULL pairs per leading value, matching SQLite's (the default backend's) historical ordering; existing API tests pin this.

### D3. Empty-string handling stays in Python

The legacy code dropped falsy first-column values (`if row[0]`) after the query; the skip-scan path keeps that exact post-filter so empty-string ids behave identically.

## Risks / Trade-offs

- [A facet with pathologically many distinct values (e.g. api_key_id in the thousands) makes thousands of probes] → still strictly cheaper than the full pass it replaces (k probes of log n vs full index scan); cardinalities here are operator-bounded.
- [Recursive CTE dialect quirks] → the seed/successor shape used is supported by PostgreSQL and SQLite (≥3.8.3); covered by tests on both backends in CI (`POSTGRES_PYTEST_TARGETS`).

## Migration Plan

Code-only; no schema change. Rollback = revert.

## Open Questions

None.
