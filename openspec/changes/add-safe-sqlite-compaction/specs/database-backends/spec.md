## ADDED Requirements

### Requirement: Owned compaction is SQLite-file only

The owned compaction command MUST accept only a file-backed SQLite database URL.
It MUST reject PostgreSQL, in-memory SQLite, and missing database files without
creating maintenance artifacts. Execution MUST also reject a symbolic-link
database path so the database and sidecar namespace cannot diverge.

#### Scenario: PostgreSQL compaction is rejected

- **WHEN** an operator requests owned compaction for a PostgreSQL URL
- **THEN** the command fails with a SQLite-only error before connecting or
  creating files

#### Scenario: In-memory SQLite compaction is rejected

- **WHEN** an operator requests compaction for an in-memory SQLite URL
- **THEN** the command fails because no replaceable filesystem database exists
