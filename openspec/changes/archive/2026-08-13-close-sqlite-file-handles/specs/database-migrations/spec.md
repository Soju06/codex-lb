## ADDED Requirements

### Requirement: SQLite maintenance releases file handles before filesystem mutation

Synchronous SQLite maintenance operations MUST explicitly close every native
connection they open after completing or rolling back its transaction. A
pre-migration backup MUST release its source and destination connections before
retention deletes an older snapshot. Recovery with `--replace` MUST release
connections used for integrity checking, dump export, and dump import before it
renames either the source database or recovered output. Correctness MUST NOT
depend on garbage collection or interpreter object-finalization timing.

#### Scenario: Backup retention deletes an old snapshot on Windows

- **GIVEN** SQLite pre-migration backups have reached their retention limit
- **WHEN** a new online snapshot is complete and retention deletes the oldest
  snapshot
- **THEN** every connection opened for the completed snapshot is explicitly
  closed before deletion
- **AND** backup rotation succeeds on platforms that prohibit deleting an open
  database file

#### Scenario: Recovery replaces a database on Windows

- **GIVEN** a file-backed SQLite database is recovered through the CLI with
  `--replace`
- **WHEN** dump export and import complete
- **THEN** the integrity-check, source, and output connections are explicitly
  closed before either database file is renamed
- **AND** the original is preserved under the corrupt-backup name while the
  recovered database is moved into the original path
