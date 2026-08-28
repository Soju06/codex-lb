# Design

## Why the hot path is untouched

`load_balancer.py`'s `hard_sticky` branch never enters the soft-reallocation
code (recovery-sleep waits, budget-pressure reallocation, TTL rebinding) by
design: a `codex_session` pin can represent live, unverifiable state (an
in-flight tool call, account-scoped uploaded files, opaque CLI turn state)
that another account cannot safely take over mid-session. Unlike the
`previous_response_id` fixes in the same problem space, there is no
stored-object replay to verify here — nothing proves the client's next
message is a safe fresh start rather than a continuation of something only
the original owner can resolve. So request-time selection must keep failing
closed for an unavailable hard owner, exactly as today.

## Why a periodic purge instead of a threshold inside selection

Threading a "give up after N hours" check into the hot-path `hard_sticky`
branch would make every selection call pay for a purge decision it usually
doesn't need, and would tangle a background-cleanup concern into
correctness-critical selection code that's already carefully scoped (see the
comment at `load_balancer.py:866-869` about never entering sticky fallback
code from the hard branch). The existing `StickySessionCleanupScheduler`
already runs leader-elected, every 300 seconds, purging prompt-cache and
bridge-session rows the same way. Adding one more purge query to that same
cycle keeps the correctness-sensitive selection path completely unchanged
and reuses infrastructure that's already tested for leader-election
correctness and background-session handling.

## What clock to gate the cutoff on

The first draft gated on `Account.reset_at`/`blocked_at` — plausible in
theory (they look like "when will/did this become unavailable" fields), but
wrong against real data: `reset_at` is frequently `None` (upstream simply
hasn't reported fresh quota data recently), and `blocked_at` is explicitly
reset to `None` whenever an account is paused (`accounts/service.py`'s
`pause_account` and the auto-pause-on-import path both pass
`blocked_at=None`). Neither field reliably answers "how long has this
specific account been broken" across all three unavailable statuses.

`StickySession.updated_at` becomes the shared durable clock by recording the
later of two events: the mapping's last use and the owner's transition from an
available status into `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`.
`AccountsRepository` refreshes hard mapping timestamps only for that boundary;
repeated writes while the owner remains unavailable do not extend the grace
period. Gating on `Account.status` (still unavailable) AND
`StickySession.updated_at < cutoff` therefore preserves the full threshold
after a fresh outage even for a long-lived session, without adding another
account status timestamp or relying on incomplete quota metadata.

## Letting a known future reset_at override the flat cutoff

`reset_at` is unreliable as the *only* clock (see above — it's frequently
absent), but when it *is* populated and still in the future, ignoring it
would be wrong in the other direction: a multi-day quota window (e.g. a
weekly cap) can easily leave a mapping unused past the 6-hour cutoff while
its owner's own stated recovery time is still days away. Purging in that
window would contradict the spec's "well past its own recovery point" —
recovery hasn't even arrived yet, let alone been passed. So the purge query
excludes any account whose `reset_at` is set and still ahead of "now",
regardless of how stale its mapping looks by the cutoff alone. This can only
ever *delay* a purge relative to the plain cutoff, never accelerate one: an
absent `reset_at` falls straight back to the cutoff-only behavior above.

## Closing the rollout gap: seeding the clock for pre-existing outages

The status-transition hook only fires on a live transition into an
unavailable status — it has nothing to hook for an account that was already
sitting in `PAUSED`/`RATE_LIMITED`/`QUOTA_EXCEEDED` before this process
started. That's exactly true the moment this change is first deployed: any
account already unavailable at that instant keeps whatever `updated_at` its
mapping had *before* the outage, which could easily already be older than
the 6-hour cutoff even though the outage itself might be minutes old. Left
alone, the very first cleanup cycle after deploy would purge that mapping —
a merely-transient (possibly brand-new) outage, purged as if it were ancient,
which is precisely the invariant this whole feature exists to prevent.

The fix is a startup-time seeding pass: for every account that is currently
unavailable, refresh its hard mappings' timestamps to now, before the
cleanup scheduler's first cycle can run. This reuses the exact same
`_refresh_hard_sticky_outage_grace` write the transition hook already makes,
just triggered by "found already unavailable at boot" instead of "just
transitioned".

The first version of this seeding ran unconditionally on every process
start, reasoning that re-seeding an already-unavailable account's mapping
only ever pushes its eligibility further out, so a frequently restarting
deployment would merely delay cleanup, never cause a premature one. That
reasoning is true for a single replica restarting occasionally, but wrong for
a shared-database, multi-replica deployment: every boot on every replica
re-seeds *all* currently-unavailable owners, not just ones that just started
this process. If deploys or autoscaling cycle faster than the 6-hour cutoff,
a durably-dead mapping's grace clock is perpetually pushed forward and it can
never age out — the exact "stuck forever" failure mode this whole feature
exists to fix, just moved from "no expiry mechanism" to "expiry mechanism
that never actually elapses".

The backfill only ever needs to run once per database, not once per process
start — after that first pass, the live per-transition hook is the sole
source of truth for every account's grace clock, so nothing subsequent needs
seeding. `runtime_sentinels` already exists for exactly this shape of
problem (see `key_fingerprint.py`'s `verify_encryption_key_fingerprint`,
which stamps an insert-if-absent sentinel the first replica to boot writes
and every other replica reads): the seeding method atomically inserts a
`hard_sticky_outage_grace_seeded` sentinel with `ON CONFLICT DO NOTHING`,
and only performs the backfill loop if that insert actually happened (i.e.
this is the first replica, ever, against this database, to see the
sentinel absent). Every later boot — same replica or a different one —
finds the sentinel already stamped, skips the backfill, and returns 0.

## Choosing the threshold

Six hours is deliberately far longer than any ordinary quota-reset window
(Codex quota windows are typically minutes to a few hours) so a transient
blip — the exact case the "does not lose its mapping" scenario protects —
never has its mapping purged mid-window. It only fires once an account has
been stuck well past when it should have recovered on its own — and, when a
`reset_at` is known, past that too — which is a strong signal something is
actually wrong (manual pause left in place, etc.) rather than ordinary quota
cycling. This is a
fixed constant, not a new setting, matching this scheduler's existing
"poll cadence is fixed; issue #1340 / PRINCIPLES.md P2" convention and the
simplicity-gate norm of defaulting new behavior on without adding
configuration surface.

## Why tombstone before delete, not just delete

A request carrying a non-empty `conversation` field sets
`require_unambiguous_account=True` (see `affinity.py`'s
`_affinity_with_payload_continuity`) precisely because `conversation` has no
dedicated owner index of its own — the hard `codex_session` mapping (or a
one-account pool) is the *only* thing that can prove who owns it. In
`run_sticky_selection_path`, once `hard_sticky` is false (no resolved
mapping) and the account pool has more than one candidate, that ambiguity
check fails the request closed. Under normal operation this branch is nearly
unreachable: by the time a request carries a non-empty `conversation`, an
earlier turn already established the hard mapping, so `hard_sticky` is true.

A straight delete breaks that invariant. The moment the mapping is gone,
`hard_sticky` becomes false on the very next request for that turn state —
and if that request also carries `conversation` (which it will, since it's a
continuation), it hits the exact ambiguity check above. Worse, it can never
recover: the only write path that could re-establish `hard_sticky` is the
normal selection flow *past* that same check, so the request is stuck
failing closed permanently, even once the original owner comes back — not
merely for the transient-blip window this whole feature is designed to
respect, but forever.

The fix is to make the purge distinguishable from "this key was never seen".
A tombstoned row (`continuity_abandoned_at` set) still causes
`get_account_id_and_abandonment` to report no owner (so `hard_sticky` is
still false, exactly as a straight delete would produce), but it also
reports `sticky_continuity_abandoned=True`. `run_sticky_selection_path` uses
that to exempt the row from the ambiguity check specifically — not because
the pool has stopped being ambiguous (it hasn't), but because we *know* this
key's owner was durably unavailable and continuity was deliberately
abandoned, which is exactly the authorization the ambiguity check exists to
require. Selection then proceeds through the normal (non-hard) path, picks a
healthy account, and the resulting `upsert` clears `continuity_abandoned_at`
as part of establishing the fresh owner — fully restoring `hard_sticky` for
that key from then on.

A tombstone that nobody ever claims is still eventually removed — after a
further grace window past its own `continuity_abandoned_at`, not the
mapping's original `updated_at` — so the row doesn't accumulate forever. At
that point a fresh request for the key falls back to the ordinary
never-seen-key fail-closed default, which is fine that long after the fact.

## Why delete, never rebind

Rebinding would require deciding *which* account to rebind to and asserting
that account can safely inherit whatever live state the mapping represents —
exactly the thing this whole subsystem says isn't verifiable for a hard
`codex_session` pin. Tombstoning-then-deleting sidesteps that entirely: the
mapping simply stops being a live pin (whether it's still present as a
tombstone or fully gone), and the next request that would have resolved it
instead goes through ordinary fresh selection, identical to a session no one
has ever pinned. This is the same effect the existing reauth/deactivated
cascade delete already produces for those two statuses — this change only
extends it to the two recoverable-but-stuck statuses, gated on elapsed time
instead of firing immediately.
