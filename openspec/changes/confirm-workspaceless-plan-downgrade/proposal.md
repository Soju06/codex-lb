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

Confirmation state is per-process in-memory, mirroring the existing
`_usage_refresh_auth_cooldowns` pattern in the same module. No new setting, no
schema change, and no migration: the confirmation threshold is a hardcoded
default (two consecutive observations), so operators have nothing to configure.

## Impact

- Affected capability: `usage-refresh-policy`.
- An expired paid account converges to `free` after two background refresh
  cycles (or two Force probes) instead of never, so the dashboard, quota data,
  and plan-based routing stop disagreeing with the account's real entitlement.
- No change for workspace-bound accounts, for upgrades, or for payloads
  reporting an unrecognized plan.
- A replica restart drops pending confirmations, which only delays a downgrade
  by one further refresh cycle; it never applies one unconfirmed.
