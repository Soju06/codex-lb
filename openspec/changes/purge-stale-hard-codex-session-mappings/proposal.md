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

## What Changes

Add a bounded exception, enforced only by a periodic background purge —
never by the hot-path selection logic, and never by rebinding:

- Account status transitions refresh a hard `codex_session` mapping's
  `updated_at` exactly when its owner first enters `PAUSED`, `RATE_LIMITED`,
  or `QUOTA_EXCEEDED`. Repeated writes while already unavailable do not extend
  the grace period.
- A new repository method,
  `StickySessionsRepository.purge_stale_hard_codex_session_mappings`, deletes
  mappings whose owner is still unavailable and whose timestamp — the later
  of last use and outage start — is before a conservative cutoff.
- The existing leader-elected `StickySessionCleanupScheduler` (already
  running every 300s) calls this once per cycle, using a fixed threshold
  (`_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS`, 6 hours) deliberately far
  longer than any ordinary quota-reset window, so a transient blip never
  loses its mapping.
- Deletion only — never rebinding. Once a stale mapping is gone, the next
  request against that session/turn state simply re-resolves fresh, exactly
  as it already does today for a request that has no mapping at all.

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

- Code: `app/modules/proxy/sticky_repository.py` (new repository method),
  `app/modules/sticky_sessions/cleanup_scheduler.py` (wires it into the
  existing periodic job).
- Tests: repository-level purge behavior (transient vs. durably-unavailable
  owners), scheduler wiring.
- API/schema: none. No new settings — the threshold is a fixed constant,
  matching this scheduler's existing "poll cadence is fixed" convention.
