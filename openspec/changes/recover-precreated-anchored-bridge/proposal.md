## Why

An HTTP Responses bridge can send a hard-continuity `response.create` and
remain completely silent before upstream emits `response.created`. The current
eventless watchdog retires the bridge because the generic pre-created replay
guard treats an anchored continuation as unsafe, even when no response event,
model output, or downstream output exists. Codex then exhausts its own retry
budget and pauses the task instead of receiving a bounded server-side recovery.

## What Changes

- Permit one same-account, same-anchor pre-created recovery after the
  eventless response-created watchdog fires.
- Keep the recovery proof narrow: hard bridge ownership, an existing
  `previous_response_id`, no response id, zero response events, no downstream
  sequence/output, and no model output.
- Keep durable operation fencing, account ownership, admission, reservation
  settlement, and the existing terminal retirement path unchanged.
- Preserve fail-closed behavior for file-pinned requests, requests with any
  response/model/downstream output, soft affinity, and subsequent retries.
- Add route-level regression coverage for a silent upstream followed by a
  successful replacement socket, plus negative coverage for unsafe continuations.

## Impact

- HTTP Responses bridge pre-created timeout/reconnect behavior.
- No public API, database schema, environment variable, or WebSocket policy
  change.
