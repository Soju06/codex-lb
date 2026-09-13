## ADDED Requirements

### Requirement: SQLite watchdog diagnostics preserve invalidated transaction cleanup
SQLite watchdog callbacks MUST NOT access connection metadata in a way that reconnects an invalidated or closed connection. An invalidated transaction MUST be able to complete rollback without watchdog diagnostics raising PendingRollbackError.

#### Scenario: Rollback after connection invalidation
- **GIVEN** a SQLite connection with an active transaction and the write watchdog installed
- **WHEN** its driver connection is invalidated and the transaction is rolled back
- **THEN** rollback completes without a diagnostic callback reconnecting the invalidated transaction
- **AND** a subsequent query on the SQLAlchemy connection succeeds
