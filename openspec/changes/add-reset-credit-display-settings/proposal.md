## Why

Operators can already see and redeem reset credits, but the signal is split across the Accounts page and top navigation with no way to tune the visual noise. There is also no automatic path for redeeming credits before they expire, so banked credits can be lost unless an operator notices them in time.

## What Changes

- Add dashboard settings for reset-credit display preferences:
  - show reset-credit count badges in the Accounts list and top navigation, default on
  - show the compact expiry countdown on the Accounts page reset-credit action, default on
- Add a dashboard setting for automatic reset-credit redemption before expiry, default off to preserve current behavior.
- Extend the Accounts Usage panel reset row to show the nearest reset-credit expiry timestamp/countdown when available.
- Reuse the existing reset-credit refresh scheduler and redeem path for automatic redemption instead of adding a separate redemption implementation.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `frontend-architecture`: Accounts and Settings UI requirements change for reset-credit display controls and Usage-panel expiry display.
- `rate-limit-reset-credits`: Reset-credit behavior changes to include operator-configurable automatic redemption before expiry.
- `database-migrations`: Dashboard settings schema changes must be represented by ORM metadata and Alembic migration.

## Impact

- Backend settings schema, repository/service/API, ORM model, and Alembic migration.
- Reset-credit refresh scheduler automatic redemption hook.
- Frontend settings schema/payload/UI, Accounts list/detail/action components, and app header.
- Existing reset-credit redeem serialization, idempotency ledger, cache invalidation, and usage refresh paths are reused.
