## Why

A `burn_first` account that is already above the soft-drain usage threshold is classified as `DRAINING` before routing-policy preference is applied, so fresh requests never consume its remaining quota even when a healthy fallback account exists. This prevents the existing 0% member-auth transition from ever becoming eligible for accounts stranded above zero.

## What Changes

- Allow an owner-free fresh request to select a `burn_first` account whose draining state is caused only by quota usage.
- Require a separately selectable healthy fallback account before admitting that usage-draining `burn_first` account.
- Continue excluding accounts draining because of errors or another health failure.
- Preserve all existing soft-sticky, hard-continuity, unresolved owner-bearing, and opportunistic routing behavior.
- Keep weighted routing to one actual selection so its winner and observability describe the account that is returned.
- Keep the member-auth automatic transition threshold at 0% usage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Refine fresh-selection ordering so a usage-only draining `burn_first` account can be exhausted safely without changing sticky ownership.

## Impact

- Account selection in the unbound and fresh soft-sticky proxy selection paths.
- Unit tests for fresh, sticky, health-tier, and fallback routing behavior.
- No API, database, configuration, or dashboard contract changes.
