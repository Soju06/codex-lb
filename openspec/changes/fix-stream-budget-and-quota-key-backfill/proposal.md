## Why

Streaming `/v1/responses` currently reapplies only the connect timeout after account selection or token refresh consumes part of the request budget. A stream that reaches the upstream with only a few seconds left can still sit idle or run against the full configured stream window instead of failing promptly with `upstream_request_timeout`.

The additional usage quota-key migration also writes durable keys through mutable runtime configuration. If the registry differs between migration time and request time, historical rows can be backfilled under keys the running app will never query, which breaks mapped-model routing until fresh usage data arrives.

## What Changes

- clamp per-attempt stream connect, idle, and total timeouts to the same remaining request budget for the initial stream attempt and the forced-refresh retry path
- backfill `additional_usage_history.quota_key` through the configured registry mapping while keeping runtime lookups compatible with raw upstream aliases
- coalesce additional-usage aliases by canonical quota before pruning stale rows during refresh
- add regression coverage for both timeout-budget propagation and deterministic quota-key backfill

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: streaming attempts must honor the remaining request budget across connect, idle, and total timeout controls
- `database-migrations`: additional usage quota-key backfill must use the configured canonical alias mapping available at upgrade time
- `query-caching`: additional-usage refresh must merge same-quota aliases before deleting persisted snapshots

## Impact

- Code: `app/modules/proxy/service.py`, `app/db/alembic/versions/20260312_000000_add_additional_usage_quota_key.py`, `app/modules/usage/repository.py`, `app/modules/usage/updater.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/unit/test_db_migrate.py`, `tests/unit/test_additional_usage_repo.py`, `tests/unit/test_usage_updater.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/database-migrations/spec.md`, `openspec/specs/query-caching/spec.md`
