## Why

PR #2132 review identified three ways a proven-rejected access token can become routable again: randomized re-encryption, a concurrent health update, and direct-dispatch consumers bypassing the canonical eligibility gate.

## What Changes

- Compare access-token material before clearing a rejection during guarded rotation.
- Preserve rejection when a noncredential status update wins the first compare-and-set.
- Apply canonical credential availability to warmup and automation dispatch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Clarify unchanged token material, concurrent status writes, and direct-consumer rejection gates.

## Impact

Account token persistence, load-balancer rejection handling, warmup and automation eligibility, and focused regression tests. No new configuration, dependency, API, or schema.
