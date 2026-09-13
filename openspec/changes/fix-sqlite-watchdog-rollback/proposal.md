## Why

SQLite watchdog diagnostics access SQLAlchemy connection metadata during rollback. On an invalidated transaction, that access attempts to reconnect and raises PendingRollbackError, preventing rollback.

## What Changes

- Skip watchdog metadata reads when a connection is invalidated or closed.
- Prove rollback completes and the connection can execute a subsequent query.

## Capabilities

### Modified Capabilities
- database-backends: Diagnostics must not reconnect an invalidated transaction during cleanup.

## Impact

Only SQLite watchdog event callbacks and their regression coverage change. Extracted from #1528; usage-limit behavior is independent.
