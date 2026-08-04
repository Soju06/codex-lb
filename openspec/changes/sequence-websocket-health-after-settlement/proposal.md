## Why

The ordering-sensitive WebSocket finalizer waits for its API-key settlement
task but ignores a `False` result. A failed primary settlement can therefore
schedule fallback release in the background while the finalizer records
account health first, violating the existing settlement-ordering invariant.

## What Changes

- Make ordering-sensitive stream settlement wait for a failed primary
  settlement's fallback release and report whether the reservation is actually
  settled.
- Keep tracked persistence ownership through ordering-sensitive fallback
  release so graceful shutdown drains both phases, including pre-start task
  cancellation.
- Record WebSocket account health and existing retry-deferred health penalties
  only after settlement or fallback release is confirmed; keep reconnect and
  retirement safety independent of persistence.
- Preserve the ordinary detached settlement path and its tracked fallback
  behavior unchanged.
- Add deterministic regression coverage for a failed primary settlement with a
  blocked fallback and for an unconfirmed fallback.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Clarify that ordering-sensitive WebSocket and retry health writes
  wait for fallback release after primary settlement failure and remain
  unapplied when neither operation confirms settlement.

## Impact

The change is limited to stream API-key settlement coordination, WebSocket
finalization, the existing retry-deferred health path, focused proxy tests, and
the API-key settlement contract. It adds no setting, dependency, schema,
migration, quota-cleanup behavior, or compact settlement behavior.
