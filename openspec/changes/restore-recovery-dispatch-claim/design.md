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

The restored expectation is **opt-in**: every signature takes
`expected_recovery_dispatch_count: int | None = None` and applies the predicate
only when a value is supplied, which is the shape
`_lock_operation_for_chunk_append` already had before #2366. No caller supplies
one on this branch, so no restored predicate is added to any statement and
request/response behaviour is byte-identical to `main` at the shipped default,
exactly as #2366's own removal was.

The `int = 0` default the pre-#2366 code used is deliberately *not* restored.
Before #2366 the request path always passed a value seeded from the row
(`request_state.operation_attempt_generation`), so a legacy row carried across
an upgrade with `recovery_dispatch_count > 0` compared equal. With that seed
deleted, a literal `0` default would make both terminal append and fallback
settlement reject such a row: it would stay `acknowledged` with an incomplete
spool, and every later retry would fail closed as an in-flight or unknown
operation. `None` keeps the fence dormant instead of wrong, and
`test_legacy_nonzero_recovery_dispatch_count_still_settles` pins both halves —
a carried-over row settles with no expectation supplied, and a stale explicit
`0` is still refused.

When #2374 wires the expectation from its own claim site, where the
post-claim generation is known, the predicate engages for exactly the callers
that claimed.

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
