## Why

Fleet consumers currently receive `lastRefreshAt`, but that timestamp tracks
OAuth credential refresh rather than the quota snapshot returned in `primary`
and `secondary`. Consumers need an explicit freshness timestamp for the usage
values without changing the existing auth-refresh contract.

## What Changes

- Add nullable `usageRefreshedAt` to each `GET /api/fleet/summary` account.
- Derive the value from the newest `recorded_at` among the standard usage
  samples already loaded for the summary, without another database query.
- Apply the existing fleet usage-visibility policy to the new field.
- Keep `lastRefreshAt` unchanged as the OAuth token-refresh timestamp.
- Cover successful Force Probe and fleet refresh writes advancing usage
  freshness without advancing auth freshness.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `fleet-summary`: Distinguish usage-snapshot freshness from OAuth token
  refresh in the per-account fleet summary contract.

## Impact

The change is limited to account-to-fleet response mapping, the fleet response
schema, focused fleet/probe regressions, and the fleet-summary specification.
It adds no migration, persisted field, database query, setting, scheduling
change, dashboard change, or dependency.
