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
  Fallback settlement uses the existing response identity, state, session,
  instance, and owner-epoch fences.
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

## Fencing a late terminal write

The fence must refuse two writes: the append abandoned at the bound that later
reaches the writer after fallback settlement published the outcome, and a second
terminal append that would rewrite an outcome already committed. It must not
refuse an ordinary first append.

Those cases cannot be told apart from `state` and `event_spool_complete`.
`_process_http_bridge_upstream_text` publishes the terminal operation state
*before* appending the terminal transcript block, so "terminal `state` with an
incomplete spool" is the shape of every ordinary operation for the entire window
in which its terminal append runs — the deferred `complete_spool=false` append
above widens that window rather than closing it. A fence inferred from it sends
every normal completion down the settlement path, leaving `completed` /
`event_spool_complete=false` / zero events, which disables transcript replay and
hard-continuity eligibility fleet-wide.

`http_bridge_operations.terminal_append_phase` records the fact directly:

- `pending` — nothing recorded for this dispatch; appends allowed.
- `appended` — a terminal append committed and awaits attempt-fenced
  finalization; further appends refused, finalization still allowed.
- `settled` — fallback settlement published the outcome without a confirmed
  append; the row is final, so appends *and* finalization are refused.

The phase fences one dispatch. Every path that starts a fresh transcript
(the server-owned ambiguous retry reset, and the rebind of a failed operation)
resets it to `pending`, and a settlement back to `acknowledged`
(continuity failure after acknowledgement) leaves it `pending` because the
operation is still live.

## Fencing the abandoned attempt's cleanup

The abandoned append still owns spooler state for its operation and runs its own
cleanup when the durable layer releases it. By then a newer attempt for the same
operation id may own that state, and clearing it would strand the retry with no
context, so it would drop its transcript and fall back to settlement. Each
terminal attempt therefore stamps a monotonic attempt token, and cleanup is a
no-op unless the token still matches.

## Timing-seam allowance

The bounded append, its deferred finalization, and the close-time drain add one
raw timeout and two raw task spawns to the event spooler, so its
`allowances.timing` entry in `openspec/specs/proxy-architecture/spec.md` moves to
`{ raw-timeout = 3, raw-task-spawn = 3 }`. These are detached lifecycle tasks
owned by the spooler rather than turn-lifecycle timing a simulation drives, which
is the same ground on which the module's pre-existing flusher task and wait are
already exempted; the bound is injectable for tests.
