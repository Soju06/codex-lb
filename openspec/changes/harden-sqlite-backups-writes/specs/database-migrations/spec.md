## ADDED Requirements

### Requirement: SQLite pre-migration backups use online snapshots

When startup creates a pre-migration backup for a SQLite database, it SHALL use
SQLite's online backup mechanism rather than copying only the main database
file. The backup SHALL include committed rows that are currently resident in WAL
state and SHALL produce a standalone SQLite database file without requiring a
sidecar WAL file.

#### Scenario: WAL-resident rows are present in the backup

- **GIVEN** a file-backed SQLite database has WAL mode enabled
- **AND** committed rows are still present in the source database WAL
- **WHEN** the pre-migration backup is created
- **THEN** those rows are queryable from the backup database
- **AND** the backup passes SQLite integrity checking
