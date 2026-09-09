# Account Routing Context

## Purpose

The normative routing contract is in [spec.md](spec.md). This context explains
why transient health is replica-local and how drained accounts return to normal
routing without becoming permanently invisible behind healthier accounts.

## Reauthentication warning state

This fork quarantines `reauth_required` accounts regardless of access-token
expiry, including foreground selection and reuse of already-open bridges.
Operator reauthentication or import repairs the account before it can serve
again. Movable soft affinity can fail over; hard account-owned continuity stays
fail-closed. This deliberately differs from upstream's unexpired-token policy.
Paused, deactivated, deleted, and security-ineligible accounts retain their
hard exclusions. Beta.5 overload isolation and quota recovery do not relax this
fork-specific rule; the normative contract remains in [spec.md](spec.md).

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
interval. The reservation carries both that timestamp token and the runtime
version it observed. Both must still match before the final lease and after
selection-state persistence; otherwise the request releases the reservation and
retries from the newer health state. Sticky selection returns one provisional
desired-state mutation instead of writing during selection. The caller applies
it only after cap classification, lease admission, state persistence, and the
probe CAS; a stale probing snapshot or fail-closed cap result therefore cannot
delete or replace the current owner. A successful rebind collapses the former
delete-plus-upsert sequence into one atomic upsert. Reserve/release remains separate from the
health-observation version used by Force Probe settlement. Recovery therefore
needs no scheduler, random sampling, or operator setting.

## Constraints and failure modes

- Eligibility, quota, cooldown, model, security, and local concurrency-cap
  checks still precede health-tier choice.
- A selectable sticky owner is retained; probing recovery uses unbound or
  fallback selection rather than moving an established owner.
- Hard-sticky fail-closed ownership does not let saturated fallback accounts
  bypass local concurrency caps. Saturated otherwise-available fallbacks return
  the stable local cap reason even when another under-cap fallback is unusable.
  Availability is compared over complete pre-cap and post-cap pools because
  opportunistic eligibility depends on what other foreground capacity exists;
  once a local cap reason is established, opportunistic error translation does
  not replace it. Nor can the post-cap selector revive an under-cap account that
  remains only a transient-backoff fallback.
- A lease race, stale persistence snapshot, or other local selection failure
  releases the provisional timestamp. After selection successfully returns a
  probe, a later caller cancellation may still postpone the next attempt by one
  quiet interval; that conservative bound cannot starve the account permanently.
- A planned sticky mutation runs after admission commits. If that database write
  fails, the request releases its local lease but retains the committed selection
  timestamp; attempting to decrement the shared runtime version would make
  concurrent health settlement ambiguous.
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

## Beta.5 overload isolation and evidence-gated quota recovery

Repeated overload rejection escalates from soft backoff to replica-local
isolation, even with intermittent successes. Isolation defaults to 1,800 seconds;
zero disables that escalation. Weighted strategies also discount recent
account-attributable errors after sufficient evidence, without changing
deterministic strategies or hard continuity. These two upstream settings retain
working defaults and allow targeted operator diagnosis without changing the
fork's memory profile or HA topology.

The integration additionally includes upstream #2078. A new usage sample is not
proof that quota recovered: applicable windows must actually have capacity.
For example, a two-hour upstream 429 followed by primary usage at 100% and weekly
usage at 40% keeps its hold rather than oscillating back to ACTIVE. Only the
marking replica may recover early from valid post-block evidence; peers honor
the persisted deadline or later persisted recovery. Historical or same-second
credit evidence cannot undo an explicit quota block. Ordinary deadline expiry
and the client's upstream error classification are unchanged.

During a later mixed-version rollout, old replicas can still execute the old
early-recovery policy. Schema compatibility is not proof that every replica has
the new evidence gate; verify all serving images after rollout. See [spec.md](spec.md)
and the `treat-usage-limit-as-quota-exhaustion` change for the precise scenarios.
