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

## What Changes

- Allow background usage refresh to persist a plan transition between two
  recognized paid plans (e.g. Plus -> Pro) for a workspace-less account, instead
  of rejecting it as an identity mismatch.
- Keep refusing workspace-less payloads that introduce `free` or an unrecognized
  plan for an account that currently holds a different plan, since those remain
  the signature of a degraded or wrong-identity usage response.
- Keep the existing workspace-conflict guard (a payload whose `workspace_id`
  differs from the account's bound workspace) unchanged.

## Impact

- Affected capability: `usage-refresh-policy`.
- Plus/Pro (and other paid-tier) upgrades and downgrades now reflect on the next
  usage refresh without a manual re-import.
- No change for workspace-bound accounts or for payloads that would drop a plan
  to `free`/unknown without workspace identity; those still skip the mutation.
