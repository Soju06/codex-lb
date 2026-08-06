## MODIFIED Requirements

### Requirement: Codex session provider retag CLI

The `codex-lb` CLI SHALL provide a `codex-sessions retag` subcommand that rewrites local Codex session metadata from one supported model provider tag to another supported model provider tag. The command MUST support `openai` and `codex-lb` provider tags, MUST reject unknown providers, and MUST reject retag requests where `--from` and `--to` are the same provider. The command MUST preserve session continuity and exact rollback evidence while avoiding duplicate full-session scans.

#### Scenario: Dry run plans JSONL and SQLite changes without writing

- **WHEN** an operator runs `codex-lb codex-sessions retag --from openai --to codex-lb --dry-run`
- **THEN** the command reads only the provider-bearing session metadata needed from each JSONL session file
- **AND** it does not scan transcript records when the bounded first metadata record is missing or malformed
- **AND** the same discovery pass determines JSONL targets and JSONL provider counts
- **AND** each `state_*.sqlite` database with a `threads.model_provider` column is queried once for planning
- **AND** the command reports the matching files and rows
- **AND** it does not create backups or mutate session metadata

#### Scenario: Confirmed retag uses exact backup and atomic JSONL replacement

- **WHEN** an operator runs `codex-lb codex-sessions retag --from openai --to codex-lb --yes`
- **THEN** each matched JSONL original is backed up before replacement
- **AND** the command MUST prefer a same-volume hard link for the JSONL backup
- **AND** it MUST fall back to a completed safe copy when a hard link cannot be created
- **AND** each matched JSONL is read once for transformation and atomically replaced with the converted file
- **AND** matched SQLite `threads.model_provider` rows are backed up through a SQLite-safe mechanism and rewritten to `codex-lb`
- **AND** a SQLite copy fallback flushes a sibling temporary database and atomically replaces the live database
- **AND** the command reports a summary of scanned and updated JSONL files and SQLite rows

#### Scenario: Retag verifies only planned mutation targets

- **WHEN** the confirmed retag finishes writing its planned targets
- **THEN** the command verifies the provider-bearing metadata of each changed JSONL file
- **AND** it verifies only SQLite databases selected by the plan
- **AND** it MUST NOT perform a second full-home JSONL transcript scan or re-query unselected SQLite databases
- **AND** it fails without reporting success if any planned source-provider metadata remains
- **AND** the CLI reports verification failure as a controlled non-zero exit while retaining the already-emitted backup identity

#### Scenario: Retag emits structured progress throughout long work

- **WHEN** discovery, fallback copying, JSONL rewriting, SQLite updating, or verification is in progress
- **THEN** the CLI emits newline-delimited structured progress containing phase, completed work, total work, unit, and a human-readable message
- **AND** long byte-processing operations emit progress often enough for a supervising process to distinguish active work from a stall
- **AND** byte counters support totals larger than 2 GiB
- **AND** a supervising process treats repeated unchanged progress as stalled rather than extending the operation indefinitely
- **AND** existing human-readable summary fields remain available after completion

#### Scenario: Non-interactive writes require explicit confirmation

- **WHEN** the command is run in a non-interactive shell without `--dry-run` and without `--yes`
- **THEN** it refuses to write session metadata
- **AND** it exits with an error explaining that `--yes` is required

#### Scenario: Codex home resolves across host runtimes

- **WHEN** `--codex-home` is provided
- **THEN** the command uses that path as the Codex data directory
- **AND** otherwise it falls back to `CODEX_HOME`, `/codex-home` in containers, a discoverable WSL Windows profile Codex directory, or `~/.codex`
