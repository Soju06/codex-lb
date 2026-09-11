# Design notes

## Where the line between "restore" and "keep deleted" falls

#2366 removed two different things under one heading. The first is a durable
concurrency primitive: an atomic one-shot claim over the `unknown` operation
row, its refund path, and the CAS parameter surface that lets a caller pin the
generation it observed. The second is request-path plumbing that carried the
observed generation from the submit path to the terminal-append path.

The primitive is expensive to re-derive and is the thing #2374 asked for by
name, so it comes back exactly as it was — the same `sqlite_writer_section()`
ordering, the same two `SELECT ... FOR UPDATE` statements (session row first,
then operation row), the same `state == "unknown"` predicate, the same spool
deletion before the `submitted` reset, and the same
`recovery_dispatch_count >= max_recovery_dispatches` refusal. Rewriting it
from the spec would be a new implementation of concurrency-sensitive code, and
the point of a partial revert is to avoid that.

The plumbing is cheap and was measurably dead: `request_submit` seeded
`request_state.operation_attempt_generation` from the operation snapshot,
`streaming.py` copied it across the account-neutral replay and into the retry
state, and `upstream_events.py` handed it back to the batcher. With no writer
advancing the column, every one of those hops moved a constant `0`. #2374
re-wires the expectation from its own claim site, where the generation is
known at claim time, so restoring the old hops now would only have to be
undone again. They stay deleted.

## Why restoring the CAS parameters without the plumbing is safe

The parameters default to `0` on every restored signature, and no caller
passes anything else on this branch, so each restored predicate reduces to
`recovery_dispatch_count == 0`. Since `claim_unknown_operation_for_recovery`
still has no production caller, no row's counter can leave `0` either, and the
predicate is satisfied by every row the release can reach — the same
tautology #2366 correctly identified, now dormant instead of deleted. Request
and response behaviour is therefore byte-identical to `main` at the shipped
default, exactly as #2366's own removal was.

Legacy rows carried over from before #2336 with a non-zero counter are the one
case where the restored predicate is *not* a tautology at the
`append_terminal_operation_event` and `_lock_operation_for_chunk_append`
sites, because those sites now pass the default `0` rather than a value seeded
from the row. That is the pre-#2336 behaviour restored, not a new hazard: the
column has been unwritten since #2336 shipped, and `_lock_operation_for_chunk_append`'s
other caller already passed no expected count. #2374's tasks include wiring
the expectation from the claim, which is what makes these sites discriminate
again.

## Interaction with the still-pending `retire-recovery-dispatch-storage`

#2366 merged its change folder without archiving it, so on `main` today the
`responses-api-compat` spec still contains *Fenced one-shot recovery dispatch*
and the pending delta holds a `REMOVED` block for it. That block is a landmine
in either archive order — `openspec archive` applied alphabetically would run
`restore-recovery-dispatch-claim` (a no-op against a spec that already has the
requirement) and then `retire-recovery-dispatch-storage`, deleting it again.

So the pending delta is narrowed here rather than left alone. Its `REMOVED`
block is dropped, and the two `MODIFIED` fragments that assert the retired ORM
mapping and the physical-column retention are dropped with it. What survives
is true and non-vacuous: with the request-path plumbing gone, the delayed
fallback settlement of a prior attempt is rejected by the operation-state and
persisted-response-identity fence alone, because nothing supplies a generation
to compare.

The `ADDED` block in this change is idempotent against the current spec text
(`openspec archive` reports "Specs already in sync"), so it lands the correct
end state whether it is archived before or after the narrowed retire change.

## `relocate-anchored-turns-across-accounts` (#2374) compatibility

#2374's delta `MODIFIES` *Fenced one-shot recovery dispatch*. Its block is a
strict superset of the text restored here: it inserts the one-dispatch bound,
the spool-clear cross-reference, a refusal paragraph pointing at its own
preconditions, and a fourth scenario. Every other character is identical, so
restoring the pre-#2366 text verbatim gives that `MODIFIED` exactly the base it
was written against, and #2374 remains applicable without a rebase.
