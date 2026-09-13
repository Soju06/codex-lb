## Why

Issue #1456 reports that a workspace-less ChatGPT Plus account whose
subscription expired to Free stays stored and displayed as `plus` forever. The
upstream usage payload correctly reports `free` once per minute, but every
refresh is discarded:

```text
Usage refresh payload identity mismatch; skipping account mutation
account_id= stored_workspace_id=None payload_workspace_id=None
stored_plan_type=plus payload_plan_type=free stored_seat_type=None payload_seat_type=None
```

The stale paid label is not cosmetic. Routing keeps trusting the stored plan and
then contradicts itself when the account cannot serve a paid-only model:

```text
Proxy preferred account unavailable error_code=no_plan_support_for_model
error=No accounts with a plan supporting model 'gpt-5.6-terra'
```

The archived `sync-paid-plan-upgrade-without-workspace` change (PR #1217, issues
#1086 and #1215) deliberately trusted only workspace-less transitions *into* a
recognized paid plan, and deliberately kept rejecting a paid -> `free`
transition, because a single `free` payload is also the signature of a degraded
or wrong-identity usage response. That decision left no path at all for a real
subscription expiry, which is an ordinary entitlement transition.

Rejecting the downgrade forever is therefore the wrong trade-off, but accepting
the first `free` payload unconditionally would give up the degraded-response
protection the archived change was written to provide.

## What Changes

- Treat a workspace-less paid -> `free` transition as a *pending* downgrade on
  first observation: the mutation is still skipped, and the observation is
  recorded per account.
- Persist the downgrade when a second consecutive workspace-less refresh of the
  same account reports the same `free` plan, since two independent
  per-account-token payloads agreeing is no longer the single-sample degraded
  signature the guard was defending against.
- Clear a pending downgrade as soon as the account reports a recognized paid
  plan again, so a transient `free` blip never accumulates toward a downgrade.
- Keep rejecting a workspace-less payload that reports an *unrecognized* plan.
  Confirmation applies to `free` only, which is the one entitlement value the
  upstream payload uses for an expired subscription.
- Leave the differing-`workspace_id` conflict guard unchanged and
  unconditional: a payload that reports another workspace's slot is still never
  trusted, no matter how many times it repeats.

- Persist the pending observation in a new per-account table
  (`account_plan_downgrade_observations`) so the observation sequence is coherent
  across every replica sharing a database, and pin each observation to the
  credential lineage that produced it so a replaced credential starts its own
  count while routine token rotation never resets one.

Confirmation state is stored per account rather than per process, modelled on the
existing `account_refresh_claims` table used for cross-replica refresh
coordination. Process-local state would make the sequence diverge whenever more
than one replica shares a database: one replica could confirm a downgrade the
cluster had already contradicted, and two `free` samples split across replicas
would never converge. The row holds no token material — only an observation
count, a non-secret digest of the account's stable seat identity, and
timestamps — and `ondelete="CASCADE"` removes it with the account. Credential
replacement (re-import or in-place reauthentication) discards pending evidence
in the same transaction that applies the fresh material.

The confirmation threshold remains a hardcoded default (two consecutive
observations), so no operator setting is introduced.

## Impact

- Affected capability: `usage-refresh-policy`.
- An expired paid account converges to `free` after two background refresh
  cycles (or two Force probes) instead of never, so the dashboard, quota data,
  and plan-based routing stop disagreeing with the account's real entitlement.
- No change for workspace-bound accounts, for upgrades, or for payloads
  reporting an unrecognized plan.
- Adds one Alembic revision creating an empty per-account table. It is
  DDL-only with a guarded, idempotent upgrade and downgrade, and it backfills
  nothing: an account with no stored evidence simply starts its count at the next
  observation.
- Pending evidence now survives a replica restart instead of being dropped, so an
  expiry converges on the next refresh rather than restarting its count. A
  downgrade is still never applied on fewer than two agreeing observations.
- Deleting an account removes its evidence through the foreign key, and
  re-importing or reauthenticating an account discards evidence gathered under
  the previous credential.
