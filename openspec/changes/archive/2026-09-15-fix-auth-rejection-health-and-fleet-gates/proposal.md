## Why

Review 5203052603 of PR #2132 identified two remaining ways to lose proven access rejection and a fleet-refresh prefilter that still treats all reauthentication warnings as unusable. These paths contradict the existing access-material and consumer-eligibility contracts.

## What Changes

- Treat rejection as stale only when access-token material changes, retaining current refresh ciphertext as an atomic write fence.
- Keep proven rejection effective when older in-flight requests later publish health or cooldown updates.
- Apply access-aware eligibility to fleet refresh and its attempted count.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Preserve access rejection across refresh-only token rotation and later health writes.
- `fleet-summary`: Include usable refresh-only warnings in fleet refresh while excluding rejected or expired access.

## Impact

Accounts repository, load-balancer health persistence, fleet refresh, and focused integration tests. No schema, setting, or dependency changes. The separately reproduced rotation post-commit publication race remains outside this change.
