# Purge stale bridge sessions on startup

## Summary

On process startup, remove persisted HTTP bridge session rows owned by the
previous process instance, plus ownerless ACTIVE/DRAINING rows with expired
leases. This prevents the first request after restart from reusing a stale
durable bridge row that causes hung recovery and silent request failures.

## Motivation

When codex-lb restarts, the in-memory WebSocket connections are gone, but
persisted durable bridge rows remain in SQLite. The first request after
restart finds these stale rows and attempts recovery/rebind, which can hang
silently - no `request_logs` entry, no assistant response, no terminal event.

The existing background cleanup scheduler eventually purges these rows, but
only after `_abandoned_bridge_retention_seconds`, leaving a window where
stale rows block capacity and cause hung first requests.

## Scope

- Delete durable bridge rows owned by this instance on startup
- Delete ownerless ACTIVE/DRAINING rows with expired leases
- Remove associated durable bridge aliases
- Preserve sticky-session mappings (they hold no stream leases)
- Do not affect other replicas' rows (multi-instance safe)
