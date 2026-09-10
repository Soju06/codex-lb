## ADDED Requirements

### Requirement: Read-only targeted session mismatch preview

The CLI SHALL provide `codex-sessions metadata-mismatches --provider PROVIDER` with optional repeated `--session-id ID` and `--json`. It MUST accept only `openai` and `codex-lb`, compare session IDs in JSONL metadata under `sessions/` and `archived_sessions/` with `threads.id` and `threads.model_provider` in `state_*.sqlite`, and report sessions whose observed tags disagree. It MUST report unsupported observed tags without rewriting them. Preview MUST NOT create backups or change session files, database rows, or `config.toml`. Discovery MUST read at most 1 MiB per JSONL header and MUST NOT parse transcript bodies.

#### Scenario: Preview a split provider tag

- **WHEN** a session has JSONL tag `openai` and SQLite tag `codex-lb` and the target is `codex-lb`
- **THEN** preview reports the session ID and both observed tags without changing files
- **AND** consistent unrelated sessions are absent from the mismatch list

### Requirement: Confirmed session-scoped metadata repair

The CLI SHALL provide `codex-sessions repair-metadata --provider PROVIDER --session-id ID --yes` with repeated session IDs and optional `--json`. Repair MUST require explicit `--yes`, reject missing IDs and unsupported observed tags before mutation, and modify only the selected sessions' provider fields. It MUST preserve transcript bytes, unrelated sessions and provider selection including `config.toml`. It MUST back up every planned JSONL file and SQLite database before any mutation, prefer hard-link JSONL backups with copy fallback, preserve exact pre-write JSONL bytes, and retain recoverable SQLite snapshots. It MUST abort when planned data changes, verify selected targets, and report failure with the backup location if a partial write occurs. JSON mode MUST emit one result on stdout and machine-readable phase progress on stderr during discovery, backup, rewrite, SQLite update and verification. Repair MUST NOT claim multi-file transactionality.

#### Scenario: Repair only the selected session

- **WHEN** a confirmed repair selects one inconsistent session
- **THEN** all its supported observed tags equal the target after verification
- **AND** exact pre-write backup evidence exists
- **AND** unrelated sessions and configuration remain unchanged

#### Scenario: Refuse unsafe inputs before writing

- **WHEN** confirmation is absent, a selected ID is missing, an observed tag is unsupported, or a planned file changes before writing
- **THEN** repair exits unsuccessfully without overwriting the unsafe target

#### Scenario: Preserve recovery evidence on partial failure

- **WHEN** a storage write or verification fails after backup
- **THEN** repair exits unsuccessfully and identifies the retained backup location
- **AND** it does not report successful repair
