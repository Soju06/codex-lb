## Why

Detached audit-log writes and fleet refreshes can still be using database sessions when graceful shutdown disposes the application engines. Audit rows can therefore be lost, and cancelled fleet requests can race teardown even though their refresh work intentionally continues in the background.

## What Changes

- Track every detached audit-log write until it completes, including failure cleanup.
- Expose bounded drains for pending audit-log writes and cancelled-request fleet refreshes.
- Run both drains during application shutdown before usage singleflight, HTTP, and database teardown.
- Report tasks that do not finish within the existing shutdown drain timeout without adding configuration or changing request latency.

## Capabilities

### New Capabilities

- `audit-logging`: define ownership and graceful-shutdown durability for asynchronous audit-log writes.

### Modified Capabilities

- `fleet-summary`: require cancelled-request fleet refreshes to remain owned and to participate in graceful shutdown.

## Impact

- **Runtime**: `app/core/audit/service.py`, `app/modules/fleet/api.py`, and `app/main.py`.
- **Tests**: audit task lifecycle, fleet refresh drain/timeout behavior, failure isolation, and shutdown ordering.
- **Operations**: graceful shutdown may wait for these existing detached tasks, bounded by `shutdown_drain_timeout_seconds`; no new setting or migration.
