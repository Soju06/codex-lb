# runtime-portability Specification

## Purpose

Define runtime portability contracts so resilience features degrade safely across supported operating systems.
## Requirements
### Requirement: Memory monitor startup remains portable across supported platforms

The resilience memory monitor MUST NOT prevent application startup on platforms where Unix-specific standard-library modules are unavailable. The system MUST resolve RSS measurement through a platform-appropriate provider when one exists, and MUST fall back to treating memory pressure telemetry as unavailable instead of crashing when no provider is available.

#### Scenario: Windows startup does not import Unix-only resource module

- **WHEN** the application starts on Windows
- **AND** the Python runtime does not provide the Unix-only `resource` module
- **THEN** the memory monitor imports successfully
- **AND** application startup continues without `ModuleNotFoundError`

#### Scenario: RSS provider unavailable does not crash request handling

- **WHEN** the memory monitor cannot resolve RSS from `psutil`, a platform API, or `resource`
- **THEN** RSS lookup returns an unavailable result without raising to callers
- **AND** memory warning and rejection checks do not crash request handling

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

### Requirement: Server CLI validates the main listener port before startup

The `codex-lb` server CLI SHALL accept integer main-listener ports in the inclusive range `0..65535` when supplied through `--port` or `PORT`, and an explicit `--port` SHALL continue to take precedence over `PORT`. The CLI SHALL reject non-integer values and integers outside that range before loading Uvicorn, importing or starting the ASGI application, running its lifespan or migrations, or creating runtime data. A rejection MUST identify `--port/PORT`, state the supported range, and include the invalid value.

#### Scenario: Out-of-range command-line port is rejected before startup

- **WHEN** an operator supplies `--port` with an integer below `0` or above `65535`
- **THEN** the CLI exits with an error that identifies `--port/PORT`, the invalid value, and the supported range `0..65535`
- **AND** Uvicorn is not loaded
- **AND** the ASGI lifespan, migrations, and runtime data creation do not run

#### Scenario: Out-of-range environment port is rejected before startup

- **WHEN** `PORT` contains an integer below `0` or above `65535`
- **AND** no `--port` flag is supplied
- **THEN** the CLI exits with an error that identifies `--port/PORT`, the invalid value, and the supported range `0..65535`
- **AND** Uvicorn is not loaded
- **AND** the ASGI lifespan, migrations, and runtime data creation do not run

#### Scenario: Non-integer listener port is rejected before startup

- **WHEN** the selected `--port` or `PORT` value is not an integer
- **THEN** the CLI exits with an error that identifies `--port/PORT` and the invalid value
- **AND** Uvicorn is not loaded

#### Scenario: Inclusive listener-port boundaries are forwarded

- **WHEN** the selected `--port` or `PORT` value is `0` or `65535`
- **THEN** the CLI forwards the same integer to Uvicorn
- **AND** port `0` retains Uvicorn's ephemeral-listener behavior

#### Scenario: Command-line port retains precedence over the environment

- **WHEN** `PORT` contains any value
- **AND** the operator supplies an in-range `--port` value
- **THEN** the CLI validates and forwards the flag value
- **AND** the environment value does not replace it

