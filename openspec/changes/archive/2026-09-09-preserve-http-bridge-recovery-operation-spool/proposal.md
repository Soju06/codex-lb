# Preserve the HTTP bridge recovery operation fence during local rebind

## Summary

An anchored HTTP Responses bridge request can fail before any replacement
session is dispatched. The local recovery path keeps the durable operation
ledger as the idempotency fence, but current main invokes its best-effort
transcript-spool reset after rebinding the in-memory session. The repository
write is scoped to the original durable session, so it returns `False` for the
replacement and the helper raises even though this call was explicitly
best-effort. A normal anchored recovery therefore becomes a
`bridge_continuity_persistence_failed` response instead of proceeding through
the existing operation rebind.

## Why

The operation row is owned by the failed durable session until
`record_operation` performs the guarded rebind. Resetting its transcript before
that handoff removes stale terminal events while the original owner fence is
still valid. Resetting after `_get_or_create_http_bridge_session` cannot match
the row, and treating a best-effort refusal as fatal makes a recoverable local
transport failure depend on an implementation detail of the replacement
session.

## What changes

- Run the optional operation-spool reset for the ordinary anchored local-error
  recovery branch while the failed session still owns the durable operation,
  before retiring or replacing that session.
- Keep the required stale-anchor replay reset fail-closed: an unavailable or
  refused reset still returns the typed `bridge_continuity_persistence_failed`
  error before an unanchored replay can be sent.
- Make the existing `required=False` reset contract real for both a missing
  reset capability and a reset refusal (and a reset exception), so the
  ordinary local rebind can continue to the fenced operation transition.
- Add a regression at the HTTP Responses bridge surface proving that the
  best-effort reset receives the original durable session identity and that a
  refused reset does not abort the anchored recovery path.

## Capabilities

### Modified capabilities

- `responses-api-compat`: an anchored local HTTP bridge recovery preserves its
  durable operation fence and does not turn an optional transcript cleanup
  refusal into a continuity failure.

## Non-goals

- No change to operation IDs, durable schema, upstream retry policy, account
  selection, or stale-anchor full-resend replay admission.
- No weakening of the required reset used before account-neutral or owner-bound
  unanchored replay.
- No automatic merge, deployment, or live-container mutation.
