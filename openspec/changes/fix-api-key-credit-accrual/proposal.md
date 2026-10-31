## Why

API-key credit limits never accrue request usage: admission, settlement and request-log reconstruction return zero for credits. A configured maximum therefore does not enforce traffic usage (#2556).

## What Changes

- Treat credit rules as local published-rate budgets, separate from subscription quota estimates.
- Add one dated text-credit rate card shared by admission, settlement and reconstruction.
- Retain fractional credits in integer counters and persist the admitted pricing basis.
- Preserve integer whole-credit API fields and existing reset, release and exactly-once accounting.
- Refuse unpriced requests and history instead of silently granting free usage.
- Migrate existing credit counters with range checks and a paused-writer requirement.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: accrue, reserve, reconstruct and enforce published-rate credit budgets.

## Impact

Changes credit accounting, one internal reservation column, migration data, tests and API-key documentation. No endpoint, setting, dashboard layout, account-capacity constant or routing algorithm is added. Existing credit rules begin enforcing usage; unsupported models and tiers require another limit type or a supported rate.
