## Why

A full-history Responses request can carry response-owned reasoning bookkeeping
without an actual continuation anchor. After upstream HTTP 401 and a failed
forced refresh, the retry loop excludes that account but retains the dispatched
payload owner. The next selection returns preferred_account_unavailable even
when another account is healthy.

## What Changes

- Apply the existing verified account-neutral replay projection to pre-visible
  HTTP authentication rejection, including permanent forced-refresh failure.
- Preserve previous-response, file, turn-state, single-account, and ambiguous
  transport ownership; retain the original error if a request cannot move.
- Cover the routed streaming path with full-history and hard-owner regressions.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: safely recover HTTP authentication rejection.

## Impact

Streaming retry and tests only; no credential, schema, or deployment changes.
