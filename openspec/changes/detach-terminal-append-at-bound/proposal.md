## Why

The terminal-append bound added by `bound-terminal-http-bridge-spool-wait`
cancelled the append task when it expired. The append can be inside a statement
or commit under `sqlite_writer_section()` at that moment. SQLAlchemy treats the
`CancelledError` as a disconnect and invalidates the connection, and the
aiosqlite handle can stay alive with its write transaction open. Every later
writer then fails with `database is locked` while `/health/ready` stays 200,
until the process restarts (issue #1981, production trace of 2026-09-19).

## What Changes

- At the bound, the batcher stops waiting for the terminal append and returns
  `persisted=False, settlement_required=True` as before, but leaves the append
  task running. The batcher already tracks it in `_terminal_append_tasks`; it
  commits or fails under `busy_timeout`.
- Caller cancellation during the bounded wait no longer cancels the append task.
- A late commit is handled by the existing `terminal_append_phase` fence:
  refused after fallback settlement, overwritten by it when it lands first.

## Capabilities

### Modified Capabilities
- responses-api-compat: The terminal-append bound must not cancel an in-flight
  durable append.

## Impact

`app/modules/proxy/http_bridge_event_batcher.py` and its tests. No setting,
schema or wire-format change. Batcher `close()` still cancels and drains tracked
tasks at shutdown. The in-process writer-slot probe (scope item 2 of #1981) is
not part of this change.
