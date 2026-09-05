# Fence stale-anchor replay admission with retry-circuit generations

## Why

The retry circuit already records hard-affinity failures durably, and the
stale-anchor recovery path captures that state before preparing a one-shot
account-neutral replay. A lookup followed by a later send is not an admission
decision: another replica or a local failure can advance the same circuit in
between. A delayed claim can therefore dispatch a replay after the circuit has
changed, or a successful response can clear a newer failure.

This is the residual retry-circuit part of PR #1867 after the admission-
generation column and model landed in #1863. It keeps the existing failure
counter and cooldown semantics while making replay admission and reset
explicitly generation-fenced.

## What changes

- Represent the captured durable/local state as a typed immutable snapshot.
- Claim the captured generation with a dialect-guarded SQLite/PostgreSQL
  `RETURNING` compare-and-set so the claim receipt is part of the write.
- Bound the claim and one timeout reconciliation attempt by the caller's
  remaining request deadline; cancellation-resistant durable calls are
  detached at that bound and remain fail-closed without a concurrent retry.
- If a settled timeout reconciliation refuses the identical claim, perform one
  bounded durable receipt lookup and adopt the claim only when generation and
  start match the request's receipt and the database clock confirms the stored
  expiry is still live; a lookup failure remains undecided and a mismatch
  remains a confirmed competing claim.
- Recheck local state both before and after the durable CAS so a same-key local
  failure wins over a delayed replay claim.
- Store a nullable claim receipt (`admission_claimed_at_epoch`,
  `admission_claimed_generation`, and `admission_claimed_until_epoch`) with a
  bounded lease. The request path carries only its relative remaining budget
  plus cleanup grace; the repository derives expiry and every live/expired
  comparison from the database clock. Direct repository callers use the
  two-hour budget plus the same grace, so a crashed owner is reclaimable
  without allowing an active replay to be purged. The migration's downgrade
  uses that database clock too and is guarded: an unexpired receipt
  refuses rollback before DDL or version stamping, while a database with no
  live receipt may drop the marker columns and remain compatible with the
  parent revision. The receipt inspection and marker drop are serialized as
  one critical section: PostgreSQL takes an `ACCESS EXCLUSIVE` table lock and
  SQLite takes `BEGIN IMMEDIATE` before inspecting receipts.
- Carry the independent `admission_generation` through local loads and delayed
  failure merges; failure observation timestamps remain merge metadata only.
- Clear a circuit only with the captured timestamp and generation, release a
  replay marker only with its claim receipt, retain local admission state on
  lookup/CAS failure, and report whether the durable clear actually matched.
- Capture the response-create attempt count with a claim and release it during
  pre-dispatch cleanup only when that count is unchanged, so an ambiguous send
  without an operation ID cannot discard ownership.
- When terminal stale-anchor handling resets a session for a same-owner retry,
  detach the claim receipt before reset and transfer its key, lease, and attempt
  fence to the retry request state. The transferred attempt fence starts from
  the retry state's own current response-create attempt count, not the source
  request's historical count.
- Fence stale retry-circuit purges on the captured failure timestamp and
  admission generation plus any captured claim receipt, so a delayed
  same-generation failure or active replay cannot be deleted by an older
  cleanup read.
- Treat a stale purge that loses its conditional fence or fails before
  confirming deletion as unknown durable state immediately, even when a
  reload finds a fresh below-threshold row: pre-created admission and its
  cooldown hint fail closed for that call, while an ordinary lookup failure
  keeps the existing local fallback behavior.
- Record the stranded claim-receipt lockout as an explicit maintainer decision
  gate; the candidate directions and current fail-closed behavior are captured
  in `design.md` without selecting a product policy.

## Scope and non-goals

This change touches retry-circuit state and its direct durable repository/
coordinator boundary, adds one nullable-column migration with a guarded
downgrade for the claim receipt, and updates the call-site deadline/type
plumbing and regression coverage. It does not alter the existing
admission-generation column, change cooldown policy, or include operation,
quarantine, replay, account-routing, attribution, or container work from the
other PR lanes.

The stale-anchor recovery behavior remains a partial vehicle for #1867; this
delta does not claim to close that broad PR or either continuity issue wholesale.
