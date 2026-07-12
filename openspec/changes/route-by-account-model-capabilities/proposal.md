# Route requests by account model capabilities

## Why

Accounts on the same ChatGPT plan can receive different Codex model and Fast
tier rollouts. A merged plan catalog is useful for client discovery but cannot
prove that every account in that plan supports every advertised capability.
At the same time, a transient catalog-fetch failure must not be interpreted as
proof that the affected account supports nothing.

## What changes

- Keep the union of successful per-account catalogs for model discovery.
- Build account-level indexes for model and service-tier selection.
- Treat the indexes as authoritative only when every active account has a
  current or retained last-known catalog.
- Degrade to the existing plan-level routing behavior while any active account
  has unknown catalog state.
- Remove retained capability data when an account is no longer active.

## Impact

- Model refresh, registry snapshots, account selection, and HTTP bridge reuse.
- No replay, previous-response continuity, transport, database, or credential
  behavior changes.

