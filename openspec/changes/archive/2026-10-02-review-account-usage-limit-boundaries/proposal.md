## Why

The local per-account usage-limit branch still authorizes some work before awaits that can invalidate the decision. Successful usage polls with no standard measurements can also leave an older observation authorizing a capped account.

## What Changes

- Recheck HTTP bridge turns and prewarm after dispatch preparation, without holding the pending-response lock.
- Fail sticky admission closed if its policy snapshot changes during affinity persistence.
- Supersede older standard measurements when a successful poll explicitly provides no standard windows for an enabled policy.
- Preserve the canonical HTTP 503 policy-denial contract on direct Responses requests.
- Remove redundant work and defensive access where the branch already has typed contracts.
- Record review findings, architectural decisions, and verified local checks.
- Integrate the latest upstream locally and reconcile routing changes and parallel migration heads without rewriting published revisions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Specify authorization after dispatch preparation and affinity persistence, and unavailable telemetry after an empty successful poll.
- `database-migrations`: Accept explicit convergence of parallel published history while rejecting unmerged migration forks.

## Impact

HTTP bridge admission and prewarm, sticky selection, usage refresh, and their regression coverage. The policy remains optional and uses the existing evaluator, cache invalidation, error envelopes, and cleanup paths. Upstream synchronization adds a migration merge revision; the review adds no settings, dependencies, or policy columns.
