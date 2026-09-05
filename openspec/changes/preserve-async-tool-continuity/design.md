## Context

Split from #2089. Maintainer required async tools in their own PR, with
the proposal stating the reference client does not emit this yet.

## Goals / Non-Goals

**Goals:**

- Track `async: true` call items and skip synthetic interrupted outputs
  for them on both WebSocket and HTTP-bridge.
- Accept a later matching output without consuming unrelated pending
  sync calls.

**Non-Goals:**

- Configuration-update / Ultra policy (#2097).
- `response.steer` and reservation locking (# slice c).
- Changing global HTTP-bridge input fingerprinting.

## Decisions

Keep pending async ids on session/continuity state. Durable pending-tool
manifests persist the synchronous subset and filter out outstanding async
calls so a later replica does not treat async work as interrupted sync
work. Unresolved async calls in a stored prefix are non-blocking for
intervening user turns.

Async manifest filtering does not waive self-contained item validation.
Validate the complete suffix before comparing the synchronous manifest,
including nonblank call IDs. Seed that validation with outstanding async
calls from the stored prefix so delayed typed outputs remain admissible.
For example, a suffix containing an async call without `call_id` rejects
the recovery proof rather than raising `KeyError` on an HTTP request.

Rejected: landing async tools inside #2089. Maintainer required a split.

The no-manifest retained-output proof must carry the prefix's outstanding
async identities and track suffix async calls separately from synchronous
calls. Validate async call/output shapes with the existing self-contained
validator, and consume only matching typed outputs. A delayed output after
a retained assistant message is fresh input; an async call is not a turn
boundary. Keep the completed-assistant boundary mandatory, since without
a manifest a call/output pair cannot prove parallel output completeness.

This extends the existing retained-output policy to the async vocabulary
introduced here. Main's synchronous proof and its fail-closed boundaries
remain the baseline. A focused shared-proof repair is preferable to a new
persisted async manifest or transcript reconstruction: neither is needed
to distinguish non-blocking async work in client-supplied full history.
Complete transcript persistence (#1900) remains separate scope.
