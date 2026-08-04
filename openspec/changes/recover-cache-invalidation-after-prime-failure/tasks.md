## 1. Regression Coverage

- [x] 1.1 Add deterministic two-replica integration regressions proving a failed baseline prime cannot absorb later `upstream_route` or `account_routing` bumps while stale routing decisions remain warm.
- [x] 1.2 Add a hermetic unit regression for failed prime, background start, callback delivery, and acknowledgement.

## 2. Poller Recovery

- [x] 2.1 Transition an uninitialized poller to conservative callback delivery before background polling starts, while preserving explicit `prime()` retry semantics.
- [x] 2.2 Update startup lifecycle documentation to describe callback-based recovery after a failed prime.

## 3. Verification

- [x] 3.1 Run the focused cache-invalidation, route-cache, model-registry startup, and bridge-reuse integration checks required for Sensitive routing/cache work.
- [x] 3.2 Run scoped lint/format checks and strict validation for the change plus affected main specs.
- [x] 3.3 Review the final diff and worktree status; record any untested or blocked checks.
