## Why

`reset_drain` currently groups weekly reset timestamps into whole-day buckets
before comparing remaining quota. Two accounts that reset minutes and almost a
day apart can therefore be treated as equivalent, allowing the later account
to receive traffic while capacity on the earlier account is about to expire.

## What Changes

- Order `reset_drain` candidates by their exact provider-reported secondary
  reset timestamp, falling back to the exact primary reset timestamp only when
  weekly reset data is unavailable.
- Keep accounts without a usable reset timestamp behind accounts with a known
  future deadline.
- Preserve the existing remaining-quota and stable account tie-breakers only
  for accounts with the same reset deadline.

## Capabilities

### Modified Capabilities

- `account-routing`: make reset-drain ordering exact instead of day-bucketed.

## Impact

- Affected code: `app/core/balancer/logic.py`.
- Affected tests: focused reset-drain selector tests.
- No API, database, migration, or dashboard schema changes.
