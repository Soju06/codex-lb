## MODIFIED Requirements

### Requirement: Whole-home retag structured progress

The retag CLI SHALL accept `--progress-json` to emit JSONL events on stderr during discovery, backup, rewrite and verification. Each event SHALL identify its phase and completed item count, and SHALL include a total when known. Events SHALL be emitted before each phase and after each processed item. If the stderr progress reader closes, the CLI SHALL stop progress writes and continue the retag through verification. The CLI SHALL retain its human-readable stdout summary. A failure after backup creation SHALL report the retained backup path and SHALL NOT claim successful completion or automatic rollback.

#### Scenario: Supervised retag

- **WHEN** an operator runs retag with `--progress-json`
- **THEN** stderr contains machine-readable phase progress without transcript contents
- **AND** stdout retains the existing summary

#### Scenario: Write failure after backup

- **WHEN** a planned write fails after backups exist
- **THEN** the command fails and reports the backup path for recovery

#### Scenario: Progress reader closes

- **WHEN** the structured-progress reader closes during a confirmed retag
- **THEN** retag completes its planned writes and verification, preserves backups and prints its stdout summary
- **AND** no further progress writes are attempted
