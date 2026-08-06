## ADDED Requirements

### Requirement: Detect partial provider-tag mismatches

The system SHALL inspect bounded JSONL session metadata and SQLite thread provider tags for the active `openai` or `codex-lb` provider and SHALL report only session IDs whose metadata contains both the active provider and exactly one opposite supported provider.

#### Scenario: JSONL and SQLite disagree for the active provider

- **WHEN** a session's JSONL metadata is tagged `codex-lb` and its SQLite thread row is tagged `openai` while `codex-lb` is active
- **THEN** the preview SHALL report that session ID and identify the SQLite row as a repair target

#### Scenario: Session is consistently owned by another provider

- **WHEN** both the JSONL metadata and SQLite thread row are tagged `openai` while `codex-lb` is active
- **THEN** the preview SHALL exclude the session

### Requirement: Repair only previewed mismatched session metadata

The system SHALL require an explicit confirmation before changing metadata and SHALL retag only the previewed eligible session IDs to the active provider. The system SHALL preserve unrelated JSONL segments, SQLite rows, and `config.toml`.

#### Scenario: Confirmed repair updates a SQLite-only mismatch

- **WHEN** the user confirms a preview containing one session whose SQLite tag is `openai` and JSONL tag is `codex-lb`
- **THEN** the system SHALL back up and update only that SQLite row to `codex-lb` and SHALL verify the row after the update

#### Scenario: No eligible mismatch exists

- **WHEN** the preview finds no eligible mismatched session IDs
- **THEN** the system SHALL perform no write and SHALL report that no targeted repair is required

### Requirement: CLI offers an independent targeted-repair workflow

The CLI SHALL expose metadata mismatch preview and session-ID-scoped repair independently of provider selection. The preview SHALL support machine-readable JSON, and the repair SHALL require explicit confirmation and explicit previewed session IDs. Neither command SHALL invoke a profile transition, a whole-home retag, or a config rewrite.

#### Scenario: Automation requests a machine-readable preview

- **WHEN** an operator runs `codex-lb codex-sessions metadata-mismatches --provider codex-lb --json`
- **THEN** the CLI SHALL emit the scanned counts and eligible mismatch IDs as JSON without changing metadata

#### Scenario: Non-interactive repair lacks confirmation

- **WHEN** a non-interactive caller invokes `repair-metadata` without `--yes`
- **THEN** the CLI SHALL refuse the write and leave all session metadata and provider configuration unchanged
