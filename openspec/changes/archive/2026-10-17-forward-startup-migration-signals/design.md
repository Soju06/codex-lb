## Context

The standard image runs a migration as a foreground child of `/bin/sh`; the distroless image runs the same child through blocking `subprocess.run`. In both cases the application CLI takes process ownership only after migration succeeds. Container stop signals target PID 1 and are not automatically forwarded to the migration child, so neither current entrypoint owns orderly interruption during that startup window.

The standard image also supports `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false`, while the distroless image intentionally always migrates before application startup. Both successful paths must continue to exec the same `app.cli` command and set the environment value to `false` before handoff so application startup does not run migration twice.

## Goals / Non-Goals

**Goals:**

- Give one Python supervisor ownership of SIGTERM and SIGINT while a startup migration is active.
- Forward every received termination signal to the active child and reap that child before the supervisor exits or launches the application.
- Make the first parent signal determine the supervisor's normalized `128 + signal` exit status and suppress application startup.
- Preserve successful startup, exact migration error status, standard-image migration bypass, distroless always-migrate behavior, and the existing application command.
- Verify behavior through the actual entrypoint process boundary with inert subprocess fixtures.

**Non-Goals:**

- Adding production timeouts, process-group signaling, an init system, dependencies, or settings.
- Changing Docker stop grace, Helm, Compose, migration SQL, Alembic, migration locks, or database behavior.
- Refactoring the application CLI or graceful-shutdown implementation.

## Decisions

### Use one Python migration supervisor

`scripts/distroless-entrypoint.py` will own migration supervision for both images. The migration-enabled shell path will exec that script before any child is started; the migration-disabled shell path will continue to export `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false` and exec `app.cli` directly.

This keeps signal behavior in one implementation and preserves the existing distinction that distroless always migrates. Duplicating traps and wait-state handling in POSIX shell was rejected because it creates two subtly different signal owners and makes interruption races harder to test consistently.

### Install handlers before spawning and latch signals

The supervisor will install SIGTERM and SIGINT handlers before creating the migration child. It will retain the first received signal for final exit status and queue signals received before the child reference is published. Once the child exists, pending and subsequent signals are forwarded with `os.kill(child.pid, signum)`; only `ProcessLookupError` is ignored because it represents the expected exit race.

The handler will not wait, poll, log, or raise. Child reaping remains in the main control flow.

### Reap once and give parent interruption precedence

The supervisor will first observe child exit with `waitid(..., WEXITED | WNOWAIT)`, which leaves the exited child unreaped so its PID cannot be reused. It will then clear the active-child reference, reap exactly once through `Popen.wait()`, restore the original signal dispositions, and decide the outcome:

- A latched parent signal exits with `128 + signal`, even if the child reports success after handling it.
- A negative child return code is normalized to `128 + abs(returncode)`.
- A positive child return code is preserved exactly.
- Only status zero sets `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false` and execs the existing application command.

This prevents an interrupted startup from launching the application and preserves fail-fast diagnostics from the migration CLI.

### Test the real launcher boundary

Integration tests will run each actual entrypoint with a temporary executable Python probe. Marker files and release gates will provide deterministic evidence for child creation, signal receipt, reaping, app suppression, exact argv/env handoff, success, and failure. Each launcher owns a new test-only process session, so cleanup can signal and, when necessary, kill the entire owned process group without depending on a child marker. Every wait is test-bounded and every process is cleaned in `finally`; production gains no timeout.

String-based launcher tests remain secondary topology guards only.

## Risks / Trade-offs

- **Signal arrives while the child is being spawned** -> Handlers are installed first and pending signals are forwarded immediately after the child reference is published.
- **Signal arrives after child exit observation** -> `WNOWAIT` keeps the PID reserved until active ownership is cleared; the later single `Popen.wait()` performs the reap without exposing a stale PID to the handler.
- **Migration child ignores termination** -> The supervisor continues to wait; the container runtime remains the hard-kill owner. No new unreviewed production timeout is introduced.
- **Shared supervisor changes the standard-image enabled path** -> Process tests cover enabled, disabled, successful, failed, and interrupted standard-image behavior, while Docker CMDs remain unchanged.
