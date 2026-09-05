## Context

Nonterminal bridge events are queued in memory, while a terminal event drains
the operation and appends its outcome synchronously. File-backed SQLite has one
writer slot and a 30-second busy timeout, so unrelated write contention can
hold live terminal delivery even though the transcript is an optional recovery
aid.

## Decision

Apply one short internal deadline around the terminal operation's pending drain
and append. The append persists the terminal data and state while keeping the
spool incomplete. Only an append observed to finish within the deadline schedules
an owner-, recovery-generation-, and state-fenced finalization that makes the
spool replayable. On expiry, cancel the append task, clear its in-memory batcher
context, and return the existing `settlement_required` result. The relay then
queues the terminal event and end marker before attempting the existing
owner/session/epoch-fenced fallback settlement, which keeps
`event_spool_complete=false`.

The deadline is an internal safety bound rather than a new operator setting.
Operators cannot make optional transcript durability block live delivery again,
and the public configuration surface remains unchanged.

## Safety

- A timed-out append is treated as having an uncertain commit acknowledgement.
  Fallback settlement uses the existing response identity, recovery generation,
  state, session, instance, and owner-epoch fences.
- A successful append inside the bound becomes replayable only through its
  attempt-fenced finalization.
- A timeout never schedules finalization, so even a late append commit keeps the
  transcript incomplete and cannot create a transient replay window.
- Losing the in-memory operation context during a terminal drain still requests
  fallback settlement.
- Cancellation cleanup releases the batcher's flush lock and operation context.

## Non-goals

- Parallelizing SQLite writers.
- Changing transcript formats or retention.
- Guaranteeing transcript durability during database contention.
