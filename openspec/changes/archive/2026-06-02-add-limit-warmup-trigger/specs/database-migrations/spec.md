## ADDED Requirements

### Requirement: Limit warm-up persistence

The database SHALL persist global warm-up settings, per-account opt-in, warm-up attempt history, and request-log source metadata.

#### Scenario: Warm-up attempt is unique per reset
- **WHEN** an attempt is stored for an account, window, and reset timestamp
- **THEN** the database enforces uniqueness for that account/window/reset tuple

#### Scenario: Existing installs remain disabled
- **WHEN** an existing database is migrated
- **THEN** global warm-up is disabled
- **AND** all existing accounts remain opted out

#### Scenario: Warm-up request logs remain separable from user traffic
- **WHEN** a warm-up request is logged
- **THEN** the request log records a source value that allows account usage summaries to exclude internal warm-up traffic
