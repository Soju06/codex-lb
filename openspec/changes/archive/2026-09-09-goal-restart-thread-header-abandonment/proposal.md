## Why

Current Codex sends both a shared process `session-id` and a distinct
`thread-id` on a self-contained goal restart. Affinity classifies that
request as `thread_header`, so the one-shot
`abandon_unavailable_legacy_owner` flag never sets and retirement CAS
never runs. The restart stays fail-closed on the unavailable legacy
owner even though the payload is account-neutral.

## What Changes

- Grant goal-restart abandonment when a thread-scoped request still
  carries a process session, not only when locality source is
  `session_header`.
- Let retirement CAS retire the raw process-session row for
  `session_header` interpretation from that thread-scoped request.
- Consult the raw process-session row as `session_header`
  interpretation so a scoped tombstone hides it from later thread-id
  turns. Explicit `turn_state` of the same text stays hard.
- Keep incremental, file-pinned, conversation-bound, and unresolved
  tool-state requests fail-closed.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `sticky-session-operations`: Current Codex `thread-id` on a
  self-contained goal restart MUST still abandon the unavailable raw
  process-session owner for `session_header` interpretation and keep
  later same-thread continuity on the replacement.

## Impact

- `app/modules/proxy/affinity.py` restart-capability gate.
- `app/modules/proxy/_load_balancer/sticky_selection.py` retirement CAS
  source check.
- `app/modules/proxy/load_balancer.py` raw-row lookup source.
- Focused affinity and sticky-selection tests.
- No API, schema, setting, dashboard, or wire-format change.
