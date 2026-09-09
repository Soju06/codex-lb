## Why

The deployed fork is based on beta.4, whose accepted-output-free HTTP bridge retry can exclude its own hard-affinity account and wait until timeout. Upstream beta.5 fixes that regression and adds native SSE framing and sustained-overload isolation; integrating the pinned release requires preserving the fork's native buffering and account quarantine contracts.

## What Changes

- Integrate `v1.25.0-beta.5` at `b54696466d8e073832b37dd0655a14ca776fc00b` into fork baseline `7982375e8a86a5d0c5caf13de0c91458b43c4165`, without committing or deploying.
- Prioritize regression verification of #2150: preserve accepted hard-owner replay and remove obsolete turn-state when legitimately switching accounts.
- Rebuild the Rust helper for native SSE capability negotiation and preserve fork-owned byte budgets, fairness, diagnostics, account quarantine, key dashboard, UTC+7 limits, import/assignment and HA deployment.
- Retain and sync beta.5's owning OpenSpec deltas for hard-owner replay, sustained-overload routing and native SSE framing.
- Evaluate post-release #2173/#2143 (native buffering/fairness) and #2078 (quota recovery) against the integrated fork. Record whether each is already covered, can be safely adapted, or should remain a separately scoped follow-up; do not pull all of main.
- Following that assessment, include #2078's evidence-gated quota recovery and regression suite. Retain the fork's native buffering/fairness implementation; document the remaining unbounded HTTP queue as follow-up work.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: preserve the fork's native WebSocket byte-budget contract alongside the new native SSE helper capability. Upstream SSE changes retain their imported owning deltas.
- `responses-api-compat`, `account-routing`, `sticky-session-operations`: sync the pinned release's existing owning deltas; do not re-author duplicate requirements in this integration.

## Impact

Python proxy routing and client code, the Rust helper, frozen dependencies, release version metadata, tests and OpenSpec documentation. The pinned range introduces no database migration or HA topology change. No commit, branch creation, push, PR, deployment, production mutation, or unrelated documentation cleanup is included. Existing untracked `simplify-install-one-liners/` is excluded.
