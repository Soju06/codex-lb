## Why

`codex-lb-cinamon` currently only supports foreground startup. That works for containers and supervised processes, but local operators who install from PyPI still need a lightweight built-in way to start the server in the background, inspect whether it is running, and stop it later without manually managing shell job control or platform-specific process tools.

## What Changes

- Keep the existing foreground startup behavior as the default when `codex-lb-cinamon` is invoked without a lifecycle subcommand.
- Add CLI lifecycle subcommands for `serve`, `start`, `status`, and `shutdown`.
- Make `start` launch a detached background server process, wait for readiness, and persist runtime metadata in a PID file.
- Make `status` and `shutdown` operate on the recorded runtime metadata and clean up stale PID files.
- Document the new lifecycle commands in the README.

## Impact

- Code: `app/cli.py`, new CLI runtime helper module
- Tests: CLI parsing and lifecycle regression coverage
- Docs: `README.md`
- Specs: new `command-line-runtime-control` capability

## Capabilities

### New Capabilities

- `command-line-runtime-control`
