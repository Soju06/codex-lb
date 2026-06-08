## ADDED Requirements

### Requirement: Codex session provider retag CLI

The `codex-lb` CLI SHALL provide a `codex-sessions retag` subcommand that rewrites local Codex session metadata from one supported model provider tag to another supported model provider tag. The command MUST support `openai` and `codex-lb` provider tags, MUST reject unknown providers, and MUST reject retag requests where `--from` and `--to` are the same provider.

#### Scenario: Dry run previews JSONL and SQLite changes without writing

- **WHEN** an operator runs `codex-lb codex-sessions retag --from openai --to codex-lb --dry-run`
- **THEN** the command scans JSONL session files under the selected Codex home
- **AND** it scans `state_*.sqlite` databases that contain a `threads.model_provider` column
- **AND** it reports the matching files and rows
- **AND** it does not create backups or mutate session metadata

#### Scenario: Confirmed retag updates both storage formats with backup

- **WHEN** an operator runs `codex-lb codex-sessions retag --from openai --to codex-lb --yes`
- **THEN** matched JSONL session provider tags are rewritten to `codex-lb`
- **AND** matched SQLite `threads.model_provider` rows are rewritten to `codex-lb`
- **AND** the command creates a backup under the selected Codex home before rewriting matched metadata
- **AND** the command reports a summary of scanned and updated JSONL files and SQLite rows

#### Scenario: Non-interactive writes require explicit confirmation

- **WHEN** the command is run in a non-interactive shell without `--dry-run` and without `--yes`
- **THEN** it refuses to write session metadata
- **AND** it exits with an error explaining that `--yes` is required

#### Scenario: Codex home resolves across host runtimes

- **WHEN** `--codex-home` is provided
- **THEN** the command uses that path as the Codex data directory
- **AND** otherwise it falls back to `CODEX_HOME`, `/codex-home` in containers, a discoverable WSL Windows profile Codex directory, or `~/.codex`
