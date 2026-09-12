## Why

When upstream resets a quota window earlier than the deadline a 429 persisted,
codex-lb keeps the account benched until that stale deadline. The persisted
`reset_at` is a prediction made at block time; an upstream-side early or
compensatory reset silently invalidates it, and nothing re-evaluates it.

A strict reset-confirmed exception for exactly this situation already exists,
but it is scoped to Free accounts and to the monthly window. A paid account
blocked on its 5h or 7d window has no path out, even while background usage
refresh is recording, every refresh interval, that the window the deadline came
from has rolled and reports available quota. Observed in production on a Pro
fleet: an upstream-wide early 7d reset left three accounts benched with
deadlines three to four days out, each one accumulating post-reset `0%` samples
that carried a brand-new window deadline, and the only remedy was a manual
reactivate. Issue #676 reported the same shape on Plus.

The plan check was never the safety property. What binds evidence to a block is
the baseline deadline match: a deadline derived from a Retry-After hint or a
model-scoped throttle matches no quota window's reset metadata, and a reset in
an unrelated window does not match this block's deadline. Free-plan scoping was
a conservative proxy for that binding because a Free account has exactly one
quota window and therefore no window ambiguity to resolve.

## What Changes

- Resolve the reset-evidence anchor by searching every quota-window slot
  (primary, secondary, monthly) for a post-block transition whose baseline
  deadline matches the account's persisted `reset_at`, instead of only the
  monthly slot for Free accounts. The account's plan no longer gates the
  exception.
- Require the whole evidence chain (baseline, before, after, and the latest
  sample) to come from the one window the anchor identified, so a reset in an
  unrelated window cannot release a block.
- Keep an account blocked when a *different* window that carries quota for its
  plan is currently exhausted, so recovery cannot hand back an account that
  would immediately 429 again. A window whose own reset has elapsed is stale
  exhaustion evidence and does not veto recovery, and a plan's zero-capacity
  slot (the Free primary artifact) is not a window for this purpose.
- Keep reset-confirmed warm-up on the monthly slot: evidence resolved from the
  primary or secondary slot is used for recovery only and is not substituted
  into the monthly before/after pair warm-up consumes.
- Bound the anchored history lookback to the newest rows per slot, so the query
  that rescues a blocked account does not get more expensive the longer it stays
  blocked.
- Add regression coverage at the scheduler path for paid early-window recovery,
  anchor mismatch, and the sibling-exhaustion and elapsed-sibling rules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Generalize the reset-confirmed recovery exception from
  the Free monthly window to whichever quota window the persisted deadline is
  anchored to, and add the sibling-window exhaustion guard.
- `account-routing`: Restate the cross-replica cooldown exception in terms of
  the anchored blocked window rather than the account's plan.

## Impact

- Affected code: background usage refresh evidence resolution and recoverable
  status reconciliation.
- Affected tests: scheduler recovery unit coverage and the repository-backed
  scheduler scope integration test.
- No API, schema, migration, setting, dependency, dashboard, or deployment
  contract changes.
