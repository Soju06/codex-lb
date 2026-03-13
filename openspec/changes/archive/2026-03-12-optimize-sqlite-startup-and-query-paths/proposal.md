## Why

SQLite-backed codex-lb instances currently pay a full `PRAGMA integrity_check` cost on every startup, even when no migration work is pending. On the live development database this blocks startup for multiple seconds before the service can accept traffic.

The request path also still has avoidable SQLite inefficiencies:

- primary-window `usage_history` queries treat `NULL` and `"primary"` as equivalent via `OR` predicates, which makes latest-row selection less index-friendly
- `request_logs` listing always joins related tables even when the request does not search account email or API key name
- default request-log ordering only has a single-column timestamp index

This change keeps current API behavior but reduces boot latency and improves SQLite query execution on the most common paths.

## What Changes

- add a configurable SQLite startup validation mode with `quick` as the default
- replace the unconditional startup `integrity_check` with mode-driven startup validation
- normalize primary-window read predicates around `coalesce(window, 'primary')` without changing stored data semantics
- add new SQLite/PostgreSQL-compatible indexes for latest usage selection and latest-first request-log ordering
- remove unnecessary `Account`/`ApiKey` joins from request-log listing/count queries when search is not using related-table fields

## Impact

- Code: `app/core/config/settings.py`, `app/db/session.py`, `app/db/sqlite_utils.py`, `app/db/models.py`, `app/modules/usage/repository.py`, `app/modules/request_logs/repository.py`
- DB: one new Alembic migration adding performance indexes
- Specs: `database-backends` and `query-caching`
- Runtime behavior: SQLite startup becomes fast-by-default while preserving explicit operator control for stricter validation
