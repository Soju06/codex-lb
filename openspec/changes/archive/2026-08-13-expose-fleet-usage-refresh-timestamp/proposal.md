## Why

Fleet consumers currently receive `lastRefreshAt` beside quota windows, but
that value is the account's OAuth token refresh time. A successful usage probe
or fleet refresh can update the quota percentages without changing it, leaving
consumers unable to determine whether the quota snapshot is fresh.

## What Changes

- Add `usageRefreshedAt` to each account in `GET /api/fleet/summary`.
- Derive it from the newest persisted usage sample already loaded for the
  account, without adding a query.
- Keep `lastRefreshAt` unchanged as the OAuth token refresh timestamp for
  backward compatibility.
- Hide both timestamps when the caller cannot view upstream usage.
- Cover force-probe and fleet-refresh paths where usage freshness advances
  independently of OAuth freshness.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `fleet-summary`: Distinguishes quota-snapshot freshness from OAuth token
  freshness in the per-account response.

## Impact

The change affects the account-to-fleet projection, the fleet response schema,
focused account/fleet integration tests, and the fleet-summary specification.
It adds no setting, database migration, query, dependency, or frontend change.
