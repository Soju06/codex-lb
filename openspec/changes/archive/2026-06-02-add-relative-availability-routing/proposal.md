## Why

Operators sometimes need to spend accounts whose weekly quota resets soon before accounts with a later reset, while still considering absolute remaining capacity. The existing capacity-weighted strategy favors accounts with more remaining credits but does not account for how soon those credits expire.

## What Changes

- Add a `relative_availability` routing strategy that scores eligible accounts by remaining secondary credits divided by time until secondary reset.
- Expose dashboard settings for the score exponent and top-K candidate cutoff.
- Route sticky fallback selection through the same relative-availability tuning as non-sticky selection.
- Log relative-availability candidate scores and winners with stable account IDs only.

## Impact

- Operators can choose a routing mode that spends soon-resetting capacity more aggressively.
- Existing routing strategies remain available and the default remains `capacity_weighted`.
- The dashboard settings table gains two idempotently migrated columns with defaults.
