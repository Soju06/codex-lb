## Purpose and scope

The HTTP bridge stores response events in a durable operation spool. During an
anchored local recovery, a failed operation must remain the single durable
identity while the in-memory bridge session is retired and a replacement is
created. The optional reset is cleanup of the failed attempt; the subsequent
`record_operation` call is the authoritative fenced rebind.

## Decision rationale

The reset is ordered before session retirement because the repository checks
the caller's durable session ID and owner epoch. Moving it later would either
silently miss the row or require an unfenced write. The ordinary local-error
branch already has a safe fallback: `record_operation` atomically clears stale
spool material when it moves a failed operation back to `submitted`. Therefore
an optional reset refusal or transient exception is logged/ignored, while the
explicitly required stale-anchor replay path remains fail-closed.

## Failure modes

- If the original owner fence is gone, the optional reset is a no-op and the
  normal operation rebind decides whether the request can proceed.
- If the required reset is unavailable or refused, recovery stops with the
  typed continuity-persistence error before any unanchored response is sent.
- A reset exception in the optional branch must not mask the original upstream
  error; the operation ledger remains the final fence.

## Example

1. An anchored operation on durable session `origin` receives a transport
   failure and enters local recovery.
2. The optional reset is called with `session_id=origin` and may return
   `False`.
3. The old session is retired, a replacement is created, and
   `record_operation` rebinds the same operation ID under the replacement
   owner. No duplicate operation is dispatched and no continuity-persistence
   error is manufactured solely by the optional cleanup refusal.

Related normative behavior lives in `openspec/specs/responses-api-compat/` and
the existing durable operation ledger contract.
