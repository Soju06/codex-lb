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
