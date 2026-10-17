## Why

A warmup request with one eligible account currently returns a top-level 401 or 429 when that account fails authentication or rate limiting, while the same failure in a larger pool is represented in the documented HTTP 200 per-account summary. Pool cardinality should not change the endpoint contract or remove the account-level diagnostic.

## What Changes

- Normalize single-account `ProxyAuthError` and `ProxyRateLimitError` failures into the existing `failed` summary entries.
- Preserve the existing summary schema, multi-account behavior, invalid-request handling, account selection, scheduling, and global exception envelopes outside warmup.
- Add production FastAPI integration coverage for both single-account failure classes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-warmup`: Clarify that ordinary per-account authentication and rate-limit failures return the structured HTTP 200 summary regardless of target-pool cardinality.

## Impact

The change is limited to the warmup service's pre-submit error normalization, its integration coverage, and the `proxy-warmup` contract. It adds no dependencies, settings, routes, or schema fields.
