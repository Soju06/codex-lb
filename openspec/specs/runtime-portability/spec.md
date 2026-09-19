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

The `agent-lb` CLI SHALL provide a `codex-sessions retag` subcommand that rewrites local Codex session metadata from one supported model provider tag to another supported model provider tag. The command MUST support `openai` and `agent-lb` provider tags, MUST reject unknown providers, and MUST reject retag requests where `--from` and `--to` are the same provider.

#### Scenario: Dry run previews JSONL and SQLite changes without writing

- **WHEN** an operator runs `agent-lb codex-sessions retag --from openai --to agent-lb --dry-run`
- **THEN** the command scans JSONL session files under the selected Codex home
- **AND** it scans `state_*.sqlite` databases that contain a `threads.model_provider` column
- **AND** it reports the matching files and rows
- **AND** it does not create backups or mutate session metadata

#### Scenario: Confirmed retag updates both storage formats with backup

- **WHEN** an operator runs `agent-lb codex-sessions retag --from openai --to agent-lb --yes`
- **THEN** matched JSONL session provider tags are rewritten to `agent-lb`
- **AND** matched SQLite `threads.model_provider` rows are rewritten to `agent-lb`
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

### Requirement: Portable ccgpt command
Supported local installations SHALL expose an executable `ccgpt` command or documented shell function backed by the repository launcher, without hardcoding secrets.

#### Scenario: Installed command
- **WHEN** agent-lb is installed and Claude Code is present
- **THEN** `ccgpt --version` or a dry run resolves the real Claude binary and the local compatibility capability

#### Scenario: Missing Claude Code
- **WHEN** the Claude binary cannot be resolved
- **THEN** `ccgpt` fails with a clear nonzero diagnostic

### Requirement: Launcher shim rides out load-balancer recovery windows

The `claude-lb-launch` intercepting shim MUST retry connection-level agent-lb
failures (refused, reset, broken pipe — errors where no HTTP response was
produced) with bounded backoff for a default budget of at least 100 seconds,
so that a watchdog-driven service recovery (~65s worst case) never surfaces a
connection error to the agent. Real HTTP responses MUST NOT be retried. Direct
first-party Anthropic auxiliary requests MUST NOT use this local-service
recovery budget. The budget MUST remain overridable via `CLAUDE_LB_SHIM_CONNECT_RETRIES`,
`CLAUDE_LB_SHIM_CONNECT_BACKOFF`, and `CLAUDE_LB_SHIM_CONNECT_BACKOFF_CAP`.
The same proxy implementation MUST support both a launcher-owned ephemeral
port that exits with its parent and an explicitly selected fixed loopback port
that remains alive without a parent watcher.

#### Scenario: LB restarts mid-session

- **GIVEN** an agent session proxied through the shim
- **WHEN** agent-lb is unavailable for up to 100 seconds
- **THEN** the shim keeps retrying the request and completes it once the
  service is back, without surfacing a 502 to the agent

#### Scenario: Real HTTP errors pass through

- **WHEN** the upstream returns an HTTP response (including 429/503)
- **THEN** the shim forwards it without retrying

#### Scenario: Direct Anthropic failure does not wait for local recovery

- **WHEN** an auxiliary request routed directly to Anthropic fails before an HTTP response
- **THEN** the shim surfaces the direct connection failure once
- **AND** does not apply the agent-lb restart backoff

#### Scenario: Launcher-owned proxy follows parent lifetime

- **WHEN** the launcher starts an ephemeral proxy for one Claude Code session
- **THEN** the proxy binds an operating-system-selected loopback port
- **AND** exits after its parent launcher process exits

#### Scenario: Shared proxy has no parent watcher

- **WHEN** launchd starts the proxy in shared mode with an explicit port
- **THEN** the proxy binds only that loopback port
- **AND** remains available until launchd stops it

### Requirement: Installed Claude clients are independent of the source checkout

The Claude client installer MUST copy its public clients, required sibling
helpers, canonical policy, and uninstall entrypoint into a versioned local
runtime bundle before activating it. Installed commands and policy MUST NOT
require access to the source checkout. Reinstalling identical content MUST reuse
the same version. Activation MUST replace the current-version link atomically
and MUST retain prior versions.

#### Scenario: Source checkout becomes unavailable

- **WHEN** the source checkout is moved after installation
- **THEN** installed `cc`, `fable`, `opus`, and `claude-lb-launch` still resolve and run
- **AND** sibling runtime doctors and canonical policy remain available locally
- **AND** the bundled installer can uninstall without the original checkout

#### Scenario: Preview makes no changes

- **WHEN** the installer runs with `--print`
- **THEN** it reports the local bundle and managed link destinations
- **AND** it does not create a bundle, links, backups, or user configuration

#### Scenario: Existing unmanaged commands are preserved

- **WHEN** installation replaces an unmanaged file or symbolic link
- **THEN** the installer preserves it at a `.pre-agent-lb` backup
- **AND** an existing backup conflict aborts before changing managed targets

#### Scenario: Uninstall preserves replacements and rollback versions

- **WHEN** the installer runs with `--uninstall`
- **THEN** it removes only recognized managed client and policy links
- **AND** preserves user replacements, backups, and installed bundle versions

### Requirement: Claude startup checks its process environment

The Claude launcher MUST check whether its working directory is readable before
starting Claude. An unreadable or deleted working directory MUST produce an
actionable error without silently changing the selected project. On systems
with process file-descriptor limits, the launcher MUST raise a low soft limit
within the existing hard limit and a bounded launcher target before spawning
children. It MUST NOT change system-wide limits or require administrator access.

#### Scenario: Working directory is unavailable

- **WHEN** Claude startup cannot read its current working directory
- **THEN** the launcher exits with a working-directory diagnostic
- **AND** explains how to select an accessible project directory
- **AND** does not start Claude in a different project

#### Scenario: Inherited file-descriptor limit is low

- **WHEN** the inherited soft file-descriptor limit is below the launcher target
- **THEN** the launcher attempts to increase its own soft limit before spawning children
- **AND** does not exceed its inherited hard limit or lower an existing higher limit
- **AND** an unsupported or rejected limit adjustment does not prevent startup
