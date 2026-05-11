## Why

Live `codex-lb-direct` stayed healthy on the main health surface, but the
request log and container logs showed a real SQLite write-lock storm between
2026-05-11 17:37:44 UTC and 2026-05-11 17:42:57 UTC. The runtime was already
using the supported SQLite live profile: one request worker, WAL, synchronous
NORMAL, and a 5 s busy timeout. The remaining gap was write-hot code paths that
still relied on plain commit behavior or retry without a shared local write
gate.

## What Changes

- centralize SQLite write retries so write-hot repository operations roll back,
  retry with bounded backoff, and serialize SQLite attempts inside the process
- apply the helper to request-log writes and model rewrites
- apply the helper to sticky-session writes and cleanup deletes
- serialize existing SQLite lock retry attempts for API-key reservation,
  durable bridge, and bridge-ring liveness writes
- add a small live snapshot probe that reports health, request-log status
  counts, container state, and recent log-pattern counts

## Impact

SQLite remains the zero-config and direct-container default. Higher concurrency
still belongs on PostgreSQL, but transient SQLite writer overlap should now be
absorbed at the write-hot boundary instead of immediately dropping request-log,
sticky-session, reservation, bridge, or liveness writes.
