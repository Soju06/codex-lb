## Why

Streaming `/v1/responses` currently reapplies only the connect timeout after account selection or token refresh consumes part of the request budget. A stream that reaches the upstream with only a few seconds left can still sit idle or run against the full configured stream window instead of failing promptly with `upstream_request_timeout`.

The additional usage quota-key migration also hardcodes a small alias list instead of using the configured registry. Deployments that override the registry can backfill historical rows under keys the runtime will never query, which breaks mapped-model routing until fresh usage data arrives.

## What Changes

- clamp per-attempt stream connect, idle, and total timeouts to the same remaining request budget for the initial stream attempt and the forced-refresh retry path
- backfill `additional_usage_history.quota_key` with the same registry-driven canonicalization used by runtime routing
- add regression coverage for both timeout-budget propagation and registry-driven migration backfill

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: streaming attempts must honor the remaining request budget across connect, idle, and total timeout controls
- `database-migrations`: additional usage quota-key backfill must use the configured registry canonicalization path

## Impact

- Code: `app/modules/proxy/service.py`, `app/db/alembic/versions/20260312_000000_add_additional_usage_quota_key.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/unit/test_db_migrate.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/database-migrations/spec.md`
