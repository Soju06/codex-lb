## Context

The bridge has no upstream idempotency/status endpoint, so an ambiguous turn
must be fenced locally. The durable operation row is the local proof. SQLite
and PostgreSQL deployments must therefore retain that row and its session while
another instance takes ownership.

## Decisions

- Scope the fingerprint hash with the normalized API-key scope instead of
  changing the public request contract or exposing a new database column.
- Treat submitted, acknowledged, and unknown operations as recoverable; only
  terminal rows may be removed by normal retention.
- Clear event rows and byte accounting in the same transaction that rebinds a
  failed operation, so a retry has a fresh transcript.
- Require a matching fingerprint before using a completed sibling as a new
  continuation anchor. A different request remains attached to its requested
  parent.
- Use a no-op Alembic merge revision to converge the operation-ledger branch
  with additive migrations already present on main.
- Treat the event spool as incomplete until the asynchronous batcher drains it;
  SQLite's table default is rebuilt explicitly because SQLite does not support
  a direct ALTER COLUMN operation.
- Run transcript retention from the existing leader-gated cleanup loop,
  draining bounded repository batches without adding a new scheduler process;
  transcript retention remains active when sticky mapping cleanup is disabled.
- Reset partial operation events before server-owned indefinite retries and
  persist deferred reasoning blocks before the visible block they precede.

## Failure modes

- If durable persistence is unavailable, the bridge remains fail-closed rather
  than dispatching an untracked duplicate.
- If a transcript is incomplete, recovery may not replay it; the existing
  bounded retry policy remains authoritative.

## Example

Two API keys submit identical JSON against the same parent response. Their
normalized scopes produce distinct operation fingerprints, so neither request
can consume the other's completion or event spool.
