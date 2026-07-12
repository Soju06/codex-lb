## 1. Implementation

- [x] 1.1 Add `_distinct_skip_scan(column, conditions)` (recursive-CTE loose index scan) and `_facet_null_exists(column, conditions)` helpers to `RequestLogsRepository`
- [x] 1.2 In `list_filter_options`, detect the no-user-filter case and compute all four facets via skip scan (nested for pair facets, NULL pairs first per leading value); keep the legacy `DISTINCT` path for filtered requests

## 2. Tests

- [x] 2.1 Extend `tests/test_request_logs_options_api.py`: multi-value facets incl. reasoning-effort pairs (NULL and non-NULL for the same model), error-code pairs, soft-deleted exclusion, empty table, and parity between unfiltered (skip-scan) and equivalent filtered (legacy) responses
- [x] 2.2 Ensure the suite runs against PostgreSQL in CI (add to `POSTGRES_PYTEST_TARGETS` if absent)

## 3. Validation & docs

- [x] 3.1 Update `openspec/specs/query-caching/context.md` with the skip-scan decision and its gate
- [x] 3.2 `openspec validate --specs`, `ruff`, `ty`, pytest on SQLite + PostgreSQL
