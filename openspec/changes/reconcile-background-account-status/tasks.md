## 1. OpenSpec And Test Coverage

- [ ] 1.1 Add the OpenSpec proposal, design, and usage-refresh-policy delta for background recovery reconciliation.
- [ ] 1.2 Add a failing unit test that proves scheduler reconciliation restores a recoverable blocked account to `active` and leaves active accounts untouched.
- [ ] 1.3 Add a failing SQLite-backed integration test that reproduces a persisted `rate_limited` account showing recovered usage while `/api/accounts` still returns the stale blocked status.

## 2. Background Reconciliation Implementation

- [ ] 2.1 Add a narrow load-balancer helper that evaluates background-recoverable account state from persisted account and usage rows without mutating live runtime state.
- [ ] 2.2 Add a scheduler reconciliation step after usage refresh that re-reads latest usage snapshots, evaluates only `rate_limited` and `quota_exceeded` accounts, and persists only recovery transitions.
- [ ] 2.3 Use optimistic persisted writes so scheduler recovery does not overwrite newer request-path status changes.

## 3. Verification

- [ ] 3.1 Update tests to green for scheduler recovery and stale-status reproduction.
- [ ] 3.2 Run the repository checks touched by CI for this change.
- [ ] 3.3 Review the final diff and address any findings before completion.
