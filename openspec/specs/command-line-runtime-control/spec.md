# command-line-runtime-control Specification

## Purpose

Define the CLI lifecycle contract for running the API server in the foreground or as a tracked background process.

## Requirements

### Requirement: Bare CLI invocation and `serve` both run the foreground server

The CLI MUST preserve backward-compatible foreground startup when invoked without a lifecycle subcommand. The explicit `serve` subcommand MUST run the same foreground server path and MUST accept the same bind and TLS options as the bare invocation path.

#### Scenario: Operator runs the bare command

- **WHEN** the operator runs `codex-lb-cinamon` with optional host, port, or TLS flags but no subcommand
- **THEN** the CLI launches the uvicorn server in the current process using the requested bind settings

#### Scenario: Operator runs `serve`

- **WHEN** the operator runs `codex-lb-cinamon serve`
- **THEN** the CLI launches the same foreground uvicorn server path used by bare invocation

#### Scenario: Foreground bind and TLS options are shared

- **WHEN** the operator runs the bare command or `serve` with `--host`, `--port`, `--ssl-certfile`, or `--ssl-keyfile`
- **THEN** the CLI applies the same option parsing and foreground runtime behavior in either entrypoint

#### Scenario: TLS flags must be paired

- **WHEN** the operator provides only one of `--ssl-certfile` or `--ssl-keyfile`
- **THEN** the CLI rejects the invocation before starting the server

### Requirement: CLI can start a tracked background server

The CLI MUST provide a `start` subcommand that launches a detached background server process using the current Python environment. The command MUST persist runtime metadata to a PID file, MUST default the PID and log files under the default runtime directory, and MUST report success only after the started server responds on `/health/live`.

#### Scenario: Background start succeeds

- **WHEN** the operator runs `codex-lb-cinamon start`
- **THEN** the CLI spawns a detached server process
- **AND** it writes runtime metadata with the PID, host, port, and log path to the PID file
- **AND** it returns success only after `/health/live` responds from that process

#### Scenario: Background start rejects duplicate tracked runtime

- **WHEN** the operator runs `codex-lb-cinamon start`
- **AND** the PID file already points to a running tracked server
- **THEN** the CLI returns a failure instead of starting a second tracked runtime

#### Scenario: Background start cleans up a stale PID file before relaunch

- **WHEN** the operator runs `codex-lb-cinamon start`
- **AND** the PID file points to a process that no longer exists
- **THEN** the CLI removes the stale PID file
- **AND** it proceeds with a fresh background start

#### Scenario: Background start cleans up after an early readiness failure

- **WHEN** the operator runs `codex-lb-cinamon start`
- **AND** the child process exits before readiness succeeds or readiness times out
- **THEN** the CLI returns a failure
- **AND** it terminates the child process when needed
- **AND** it removes the PID file instead of leaving stale runtime metadata behind

### Requirement: CLI reports runtime state for tracked background servers

The CLI MUST provide a `status` subcommand that reports whether the tracked background process is still running, and it MUST remove stale PID files when the recorded process no longer exists.

#### Scenario: Status reports a running background server

- **WHEN** the operator runs `codex-lb-cinamon status`
- **AND** the recorded PID is still alive
- **THEN** the CLI reports the server as running
- **AND** it includes the tracked PID, host, port, and log path

#### Scenario: Status cleans up a stale PID file

- **WHEN** the operator runs `codex-lb-cinamon status`
- **AND** the PID file points to a process that no longer exists
- **THEN** the CLI removes the stale PID file
- **AND** it reports that no tracked background server is running

### Requirement: CLI can stop a tracked background server

The CLI MUST provide a `shutdown` subcommand that reads the tracked PID file, sends the server a termination signal, waits for it to stop, and removes the PID file after the process exits.

#### Scenario: Shutdown stops a running tracked server

- **WHEN** the operator runs `codex-lb-cinamon shutdown`
- **AND** the PID file points to a running tracked server
- **THEN** the CLI sends that process a termination signal
- **AND** waits for the process to exit
- **AND** removes the PID file after shutdown completes

#### Scenario: Shutdown removes stale runtime metadata

- **WHEN** the operator runs `codex-lb-cinamon shutdown`
- **AND** the PID file points to a process that no longer exists
- **THEN** the CLI removes the stale PID file
- **AND** reports that no tracked background server is running
