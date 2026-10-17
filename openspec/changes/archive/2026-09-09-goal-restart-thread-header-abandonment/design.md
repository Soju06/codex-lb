## Context

`#1679` / `#1680` added a proof-gated exception that retires an
unavailable raw `codex_session` owner for `session_header`
interpretation. `#1703` then made `thread-id` the winning locality
source for current Codex. The two compose incorrectly: the flag and
CAS both require `sticky_source == "session_header"`, which current
Codex never is.

The raw compatibility row is the process-session key. Looking it up
with `continuity_source=thread_header` treats a `session_header`
tombstone as a live hard owner, so even a successful session-only
restart is undone by the next thread-id turn.

## Goals / Non-Goals

**Goals:**

- Account-neutral goal restart with `session-id` + `thread-id` retires
  the unavailable raw owner for process-session interpretation and
  routes to a replacement.
- Later same-thread turns without a new hard owner stay on that
  replacement.
- Explicit `turn_state` of the same text stays hard-bound.

**Non-Goals:**

- Changing file-pin, previous-response, conversation, or tool-state
  fail-closed ownership.
- Making `thread_header` an abandonment scope on the raw row.
- Dashboard, settings, or schema changes.

## Decisions

- Grant `abandon_unavailable_legacy_owner` for `thread_header` only
  when a process session is also present. Thread-only clients have no
  process-session raw row to retire.
- Allow retirement CAS when request source is `thread_header`. The
  write remains `abandonment_scope=session_header`.
- Load the raw `legacy_sticky_key` with `continuity_source=session_header`.
  That lookup is process-session interpretation, not thread identity.

**Alternative considered:** keep CAS gated on request source and only
set the flag. Rejected because the CAS would still not run.

**Alternative considered:** abandon the raw row for every source.
Rejected because colliding explicit `turn_state` must stay hard.

## Risks / Trade-offs

- [Risk] A thread-header request could retire a raw row that was
  written as turn-state with equal text. → Mitigation: CAS still
  writes `session_header` scope only; turn-state lookup of that text
  keeps the stored owner.
- [Risk] Existing tests only exercise `session_id` without `thread-id`.
  → Mitigation: add the missing header combination next to those tests.
