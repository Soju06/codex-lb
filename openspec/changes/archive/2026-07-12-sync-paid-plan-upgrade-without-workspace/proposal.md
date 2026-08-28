## Why

Issue #1086 reports that after upgrading a ChatGPT account from Plus to Pro,
codex-lb keeps showing the account as Plus. The usage refresh payload carries
the correct new plan, but the account mutation is skipped with:

```text
Usage refresh payload identity mismatch; skipping account mutation
stored_workspace_id=None payload_workspace_id=None
stored_plan_type=plus payload_plan_type=pro
```

`_payload_mismatches_account_slot` treats any `plan_type` difference on a
workspace-less account as a slot/identity mismatch. The usage payload, however,
carries no independent account identifier and is fetched per-account token, so
`plan_type` alone cannot establish identity. A transition between two recognized
paid plans (Plus -> Pro) is a legitimate upgrade, not a mismatch, so the guard
wrongly blocks it and the stored plan never updates until a manual re-import.

Issue #1215 exposes the same guard on a Free -> Plus upgrade. The payload is
fetched with that account's token and reports a recognized paid plan, but the
guard only trusts paid -> paid transitions, so Force probe discards the new
plan until the operator signs in again.

## What Changes

- Allow background and forced usage refresh to persist a transition from Free
  to a recognized paid plan (e.g. Free -> Plus), as well as transitions between
  recognized paid plans, for a workspace-less account.
- Keep refusing workspace-less payloads that introduce `free` or an unrecognized
  plan for an account that currently holds a different plan, since those remain
  the signature of a degraded or wrong-identity usage response.
- Keep the existing workspace-conflict guard (a payload whose `workspace_id`
  differs from the account's bound workspace) unchanged.

## Impact

- Affected capability: `usage-refresh-policy`.
- Free-to-paid and paid-tier transitions now reflect on the next usage refresh
  or Force probe without a manual re-import.
- No change for workspace-bound accounts or for payloads that would drop a plan
  to `free`/unknown without workspace identity; those still skip the mutation.
