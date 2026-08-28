## Why

`GET /api/request-logs/options` runs four unbounded `SELECT DISTINCT` statements over `request_logs` (`app/modules/request_logs/repository.py:483-506`) — account ids, `(model, reasoning_effort)`, api-key ids, `(status, error_code)`. When the dashboard's filter panel loads with no active filters (the default), each statement is a full index/table pass on PostgreSQL, which has no loose index scan — four whole-table passes per options load, polled every 30 s while the logs page is open, degrading forever as logs accumulate.

## What Changes

- When the options request carries no user-supplied filters (no `since`/`until`/account/api-key/model/effort constraints), each facet is computed with a recursive-CTE loose index scan (skip scan): O(distinct values) indexed probes instead of a full pass. Pair facets iterate the leading column via skip scan and resolve the second column per value (including a NULL-presence probe), matching the existing facet indexes.
- Filtered requests keep the existing `DISTINCT` shape — user filters already bound those scans.
- Result sets, filtering semantics (`deleted_at IS NULL`, status self-filter exemption), and response ordering are unchanged.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: extend the hot-path query-shape contract — unfiltered request-log filter-option facets MUST NOT perform full DISTINCT passes over `request_logs`; they MUST use loose-index-scan probes while returning identical option sets.

## Impact

- **Code**: `app/modules/request_logs/repository.py` (`list_filter_options` + skip-scan helpers). No API/schema change, no migration.
- **APIs**: `GET /api/request-logs/options` responses byte-identical; latency improves on large tables.
- **Tests**: existing options API tests remain the parity oracle; add cases exercising the skip-scan path with multi-value facets, NULL second columns, and empty tables.
