## ADDED Requirements

### Requirement: Bounded whole-home retag plan

The whole-home retag CLI SHALL build one plan from JSONL metadata and one grouped provider-count query per eligible state database. JSONL discovery SHALL read at most 64 KiB per file, SHALL recognize consecutive leading legacy provider records or a leading `session_meta` record, and SHALL leave transcript contents outside that metadata unchanged. Invalid or oversized initial metadata SHALL fail before backup or mutation. After recognized legacy metadata, an incomplete record at the byte limit SHALL remain part of the unchanged transcript tail. Discovery SHALL retain the existing `sessions` directory scope. Dry runs SHALL reuse plan counts without rescanning. Confirmed writes SHALL back up before mutation and verify only planned targets. JSONL backups SHALL prefer hard links and fall back to copies, with atomic replacement of rewritten files. SQLite backups and write fallbacks SHALL retain their WAL-aware behavior. Configuration and unrelated sessions SHALL remain unchanged.

#### Scenario: Large transcript dry run

- **WHEN** retag previews a home containing supported leading metadata followed by large transcripts
- **THEN** discovery reads at most 64 KiB from each JSONL file and one grouped count query from each eligible state database
- **AND** the summary reports matching files and rows without a second scan or backup

#### Scenario: Confirmed planned mutation

- **WHEN** an operator stops Codex and confirms retag
- **THEN** the command creates recoverable JSONL and SQLite backups before writing
- **AND** only planned metadata is rewritten and verified
- **AND** transcript bytes, configuration and unrelated sessions remain unchanged

### Requirement: Whole-home retag structured progress

The retag CLI SHALL accept `--progress-json` to emit JSONL events on stderr during discovery, backup, rewrite and verification. Each event SHALL identify its phase and completed item count, and SHALL include a total when known. Events SHALL be emitted before each phase and after each processed item. The CLI SHALL retain its human-readable stdout summary. A failure after backup creation SHALL report the retained backup path and SHALL NOT claim successful completion or automatic rollback.

#### Scenario: Supervised retag

- **WHEN** an operator runs retag with `--progress-json`
- **THEN** stderr contains machine-readable phase progress without transcript contents
- **AND** stdout retains the existing summary

#### Scenario: Write failure after backup

- **WHEN** a planned write fails after backups exist
- **THEN** the command fails and reports the backup path for recovery
