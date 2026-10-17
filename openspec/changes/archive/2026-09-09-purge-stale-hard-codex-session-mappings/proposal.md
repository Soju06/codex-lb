# Purge hard codex_session mappings pinned to a durably unavailable owner

## Why

A `codex_session`-kind sticky mapping is deliberately hard: per
`sticky-session-operations`, when its owner account becomes unavailable
(rate-limited, quota-exceeded, paused), selection fails closed instead of
reallocating to a healthy account, and the mapping is neither deleted nor
rebound. This is correct — the mapping can represent live, unverifiable
session state (mid-flight tool calls, account-scoped state) that isn't safe
to move to a different account mid-session.

But today that protection has no expiry. If the owner never recovers (stuck
rate-limited well past its own `reset_at`, or paused and never resumed), the
mapping is stuck forever and every future request against that session/turn
state fails closed with `previous_response_owner_unavailable`
("Hard affinity owner account is unavailable") indefinitely, with no
automatic recovery path. We hit this in production and had to manually
delete ~250 stale mappings directly in the database to unblock it.

A request carrying a non-empty `conversation` field has no owner index
besides this same mapping (see `sticky-session-operations`'s
`require_unambiguous_account` behavior) — it has no other way to prove who
owns the conversation. If the mapping were simply deleted outright and the
account pool has more than one account, such a request would fail closed
permanently on the very next attempt, with no way to ever recover even after
the original owner comes back, because nothing on that path can re-create
the row it needs in order to stop failing closed. The purge below is
designed around that constraint from the start (see "tombstone, then
delete" below), not merely a straight delete.

## What Changes

Add a bounded exception, enforced only by a periodic background purge —
never by the hot-path selection logic, and never by rebinding:

- Account status transitions refresh a hard `codex_session` mapping's
  `updated_at` exactly when its owner first enters `PAUSED`, `RATE_LIMITED`,
  or `QUOTA_EXCEEDED`. Repeated writes while already unavailable do not extend
  the grace period.
- A new repository method,
  `StickySessionsRepository.purge_stale_hard_codex_session_mappings`, retires
  mappings whose owner is still unavailable and whose timestamp — the later
  of last use and outage start — is before a conservative cutoff. This is a
  two-phase, never a one-shot delete:
  1. **Tombstone**: the row's new `continuity_abandoned_at` column is set
     instead of deleting the row. Selection treats a tombstoned mapping as
     having no owner (same as if the row were gone), but — critically —
     recognizes it as *deliberately abandoned* rather than *never seen*, so a
     `conversation`-continuity request against that key is allowed to pick a
     fresh account instead of failing closed forever (see "Why tombstone
     before delete" in `design.md`).
  2. **Delete**: once a tombstone has sat unclaimed for a further cutoff
     window, it's dropped outright. By then a fresh request for that key is
     fine falling back to the same conservative fail-closed default as a key
     that was never seen.
- The existing leader-elected `StickySessionCleanupScheduler` (already
  running every 300s) calls this once per cycle, using a fixed threshold
  (`_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS`, 6 hours) deliberately far
  longer than any ordinary quota-reset window, so a transient blip never
  loses its mapping.
- Never rebinding, at either phase. Once a mapping is tombstoned or deleted,
  the next request against that session/turn state simply re-resolves fresh
  (and, if it establishes a new owner, that write clears the tombstone),
  exactly as it already does today for a request that has no mapping at all.
- A known, future `Account.reset_at` overrides the flat cutoff: the mapping
  survives until after the owner's own stated recovery point, even if it has
  long since gone stale by the cutoff alone. `reset_at` only ever narrows
  eligibility (delays a purge) — it never widens it when unset, so the fixed
  cutoff remains the fallback for the common case where `reset_at` isn't
  populated.
- At process startup, every account that is already `PAUSED`,
  `RATE_LIMITED`, or `QUOTA_EXCEEDED` gets its hard mappings' grace clock
  seeded to now — but only *once ever per database*, not once per process
  start. The status-transition hook above only fires on a live transition,
  so it never runs for an outage that predates this process (e.g. one that
  began minutes before a deploy) — without seeding, that mapping's stale
  pre-existing `updated_at` could make the very first cleanup cycle after
  upgrade treat a brand-new outage as ancient history. All replicas share one
  database, so a durable marker in the existing `runtime_sentinels` table
  (the same insert-if-absent mechanism the encryption-key fingerprint check
  already uses) records that this backfill has run; every later boot, on any
  replica, sees the marker and skips it. Without that marker, a deployment or
  autoscaling cadence shorter than the purge cutoff would re-seed on every
  boot and a durably-dead mapping could never age out.

`load_balancer.py`'s `hard_sticky` selection branch is untouched. The
correctness invariant it protects (never reallocate mid-flight to an
unverified account) is preserved; this only changes what happens to an
already-abandoned mapping between requests.

## Capabilities

### Modified Capabilities

- `sticky-session-operations`: a hard `codex_session` mapping whose owner has
  been durably unavailable (not merely transiently rate-limited) for well
  past its own recovery point MUST eventually be purged by the periodic
  cleanup job, never reallocated by request-time selection.

## Impact

- Code: `app/modules/proxy/sticky_repository.py` (new repository method,
  tombstone-then-delete purge, abandonment-aware reads), `app/db/models.py`
  (new `StickySession.continuity_abandoned_at` column),
  `app/modules/proxy/_load_balancer/sticky_selection.py` (ambiguous-owner
  check bypass for a tombstoned mapping), `app/modules/sticky_sessions/cleanup_scheduler.py`
  (wires the purge into the existing periodic job), `app/modules/accounts/repository.py`
  (outage-start refresh on status transitions plus the one-time startup
  seeding method), `app/main.py` (calls the startup seeding once during app
  boot).
- Tests: repository-level purge behavior (transient vs. durably-unavailable
  owners, future `reset_at`, tombstone-then-delete phases, one-time startup
  seeding), selection-level coverage that a tombstoned mapping unblocks a
  `conversation`-continuity request instead of failing closed, scheduler
  wiring.
- API/schema: one new nullable column, `StickySession.continuity_abandoned_at`
  (migration `20260727_000000_add_sticky_session_continuity_abandoned_at`).
  No new settings — the outage clock still reuses `StickySession.updated_at`;
  the threshold is still a fixed constant, matching this scheduler's existing
  "poll cadence is fixed" convention; the one-time startup-seeding marker
  reuses the existing `runtime_sentinels` table rather than adding a setting.
