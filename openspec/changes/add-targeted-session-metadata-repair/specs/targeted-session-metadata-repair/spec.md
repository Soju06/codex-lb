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

### Requirement: ProviderSwitcher offers an independent targeted-repair action

ProviderSwitcher SHALL offer a metadata consistency action independent of provider selection. It SHALL present the preview result and SHALL not invoke a profile transition, a whole-home retag, or a config rewrite.

#### Scenario: User declines the repair confirmation

- **WHEN** ProviderSwitcher displays a non-empty mismatch preview and the user declines confirmation
- **THEN** the system SHALL leave all session metadata and provider configuration unchanged
