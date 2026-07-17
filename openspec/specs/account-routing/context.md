# Account Routing Context

## Purpose

The normative routing contract is in [spec.md](spec.md). This context explains
why transient health is replica-local and how drained accounts return to normal
routing without becoming permanently invisible behind healthier accounts.

## Replica-local soft health

Error counts, backoff, health tiers, and probe streaks are advisory signals.
They deliberately stay in memory: a different replica may have observed a
different network path, and persisted `status`, `reset_at`, and `blocked_at`
remain the authoritative cross-replica gates.

An account moves from draining to probing only after the fixed quiet period.
Probing is validation, not a permanent low-priority state. Health-tier-aware
selection therefore gives the oldest due probing account one bounded admission
opportunity when healthy accounts would otherwise mask it. The existing
selection timestamp supplies the cadence and fair ordering. Unbound and fallback
sticky selection reversibly reserve that timestamp under the runtime lock before
sticky database work, preventing concurrent requests from consuming the same
interval. The timestamp remains provisional through the final local lease gate
and selection-state persistence, and is committed only when selection returns
the probe. Reserve/release is deliberately separate from the health-observation
version used by Force Probe settlement. Recovery therefore needs no scheduler,
random sampling, or operator setting.

## Constraints and failure modes

- Eligibility, quota, cooldown, model, security, and local concurrency-cap
  checks still precede health-tier choice.
- A selectable sticky owner is retained; probing recovery uses unbound or
  fallback selection rather than moving an established owner.
- Hard-sticky fail-closed ownership does not let saturated fallback accounts
  bypass local concurrency caps. Saturated otherwise-available fallbacks return
  the stable local cap reason even when another under-cap fallback is unusable.
- A lease race, stale persistence snapshot, or other local selection failure
  releases the provisional timestamp. After selection successfully returns a
  probe, a later caller cancellation may still postpone the next attempt by one
  quiet interval; that conservative bound cannot starve the account permanently.
- A failed real request can drain the account again through the ordinary error
  path. Recovery never permits replay after downstream output is visible.
- Restarting a replica clears advisory health as before; persisted account
  status is unchanged.

## Example

Accounts A and B are healthy while C is probing after an upstream incident.
C's last selection is older than the quiet interval, so the next unbound
health-tier-aware selection admits C once. Existing sessions on A and B stay in
place. Budget and account routing-policy preferences cannot mask this bounded
recovery pass. A successful request advances C's local probe streak; C is not due for
another bounded admission until the interval elapses. Three successful
observations restore healthy routing, while an intervening failure restarts
recovery.

## Operational notes

The dashboard Force Probe action can accelerate validation on the replica that
handles the operator request. Only an accepted 2xx probe contributes to local
recovery; operators should inspect `probe_status_code` when an account remains
unused. Non-2xx results, persistent quota exhaustion, and high usage correctly
keep the account out of healthy routing. Successful settlement reloads standard
usage and applies the same weekly/monthly and zero-primary-capacity normalization
as ordinary selection, so plan-specific windows cannot be omitted, mistaken for
short windows, or evaluated for a quota the plan does not have.
Settlement is discarded if newer replica-local runtime activity arrives while
that snapshot is loading, preventing an older probe success from clearing a
later failure.
