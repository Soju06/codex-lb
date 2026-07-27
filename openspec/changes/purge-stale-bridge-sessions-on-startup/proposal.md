# Purge stale bridge sessions on startup

## Summary

On process startup, remove ordinary persisted HTTP bridge session rows owned
by the previous process instance, plus ownerless ACTIVE/DRAINING rows with
expired leases whose activity predates the abandoned-row retention cutoff. Preserve a recent,
server-namespaced account-neutral recovery row as ownerless DRAINING restart
proof until that same retention cutoff. This prevents stale stream reuse
without discarding the task-specific account ownership needed after recovery.

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
- Retain recent server-namespaced account-neutral recovery rows as ownerless
  DRAINING without refreshing their activity age
- Delete ownerless ACTIVE/DRAINING rows with expired leases once they pass the
  abandoned-row retention cutoff
- Remove associated durable bridge aliases
- Preserve sticky-session mappings (they hold no stream leases)
- Do not affect other replicas' rows (multi-instance safe)
