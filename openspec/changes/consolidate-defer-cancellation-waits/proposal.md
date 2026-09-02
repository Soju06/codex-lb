# Consolidate defer-cancellation waits into the canonical helper

## Why

Six hand-rolled copies of the defer-cancellation wait loop drifted apart
until the one copy without the busy-spin guard livelocked production
(2026-08-30, #1968). #1955 moved the hardened http-bridge/retry copy into
`app/core/utils/shared_future.py`; this change routes the remaining copies
(proxy api, model-sources forwarding, websocket helpers, db session
`_shielded`) and the inline `api_key_usage` shield loops through it, so a
divergent copy cannot be reintroduced silently. CodeRabbit review of #1993
also flagged that the `command_timeout` requirement overpromised an
unconditional lock-release bound: asyncpg's post-timeout cancellation
handshake itself talks to the server, so a fully blackholed peer can outlive
the bound — the requirement is qualified accordingly.

## What Changes

- Route every defer-cancellation wait through the canonical shared-future
  helper: alias imports where the shape matches (websocket helpers),
  two-line adapters where callers consume a bool marker or re-raise
  (proxy api, forwarding, db session `_shielded`), and
  `wait_on_shared_future` inside the site-specific `api_key_usage` loops.
- Surface the deferred-cancellation marker uniformly: the api/forwarding/db
  shapes now report a level-cancelled scope (previously silently suppressed
  until the caller's next checkpoint) exactly as the canonical helper does.
- Qualify the `database-backends` statement-bound requirement: best-effort
  against a blackholed peer; lock discipline stays the primary defense.
- Pin the alias imports to the canonical implementation with an
  identity-equality regression test.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: defer-cancellation waits share the canonical
  implementation; the deferred-cancellation marker surfaces through every
  marker shape.
- `database-backends`: qualify the `command_timeout` lock-release claim as
  best-effort against a blackholed peer; lock discipline is not relaxed.

## Impact

- No API or contract changes. Cleanup ownership semantics unchanged: owned
  tasks are never cancelled by waiters; owned-task cancellation and
  exceptions propagate unchanged.
- Level cancellation during api/forwarding/db defer waits now surfaces as
  the marker after cleanup (deterministic re-raise point) instead of at the
  caller's next checkpoint — same eventual outcome, strictly harder to
  mis-handle.
- Net negative line count; no new settings.
