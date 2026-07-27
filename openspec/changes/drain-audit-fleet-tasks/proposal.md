## Why

Detached audit-log writes and fleet refreshes can still be using database sessions when graceful shutdown disposes the application engines. Audit rows can therefore be lost, and cancelled fleet requests can race teardown even though their refresh work intentionally continues in the background.

An in-flight request can also outlive `shutdown_drain_timeout_seconds`. Without an admission cutoff, that late handler can create audit or fleet work after shutdown has already observed both task registries as empty, recreating the same resource-teardown race.

## What Changes

- Track every detached audit-log write until it completes, including failure cleanup.
- Track every fleet refresh from task creation, before caller cancellation can affect ownership.
- Close a synchronous audit/fleet task-admission barrier immediately after the in-flight drain attempt and keep it closed through resource teardown.
- Skip and report post-cutoff asynchronous audit writes, and reject post-cutoff fleet refreshes with the existing dashboard `503 service_unavailable` envelope before creating resource work.
- Expose bounded drains for pending audit-log writes and fleet refreshes.
- Run both drains during application shutdown before usage singleflight, HTTP, and database teardown.
- Report tasks that do not finish within the existing shutdown drain timeout without adding configuration or changing request latency.

## Capabilities

### New Capabilities

- `audit-logging`: define ownership and graceful-shutdown durability for asynchronous audit-log writes.

### Modified Capabilities

- `fleet-summary`: require every accepted fleet refresh to remain owned from creation and to participate in graceful shutdown.

## Impact

- **Runtime**: `app/core/shutdown.py`, `app/core/audit/service.py`, `app/modules/fleet/api.py`, and `app/main.py`.
- **Tests**: audit task lifecycle and post-cutoff rejection, fleet ownership/503/drain behavior, failure isolation, and late-cancellation shutdown ordering through HTTP middleware.
- **Operations**: graceful shutdown may wait for these existing detached tasks, bounded by `shutdown_drain_timeout_seconds`; no new setting or migration.
