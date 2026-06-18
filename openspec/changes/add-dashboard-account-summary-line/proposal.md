## Why

Operators with many shared accounts cannot quickly tell how many accounts are registered and how many are currently usable from the dashboard without manually scanning the account cards.

## What Changes

- Add a compact account summary line to the dashboard `Accounts` section header.
- Show total registered account count, active account count, and unavailable account count.
- Keep the dedicated Accounts page and backend dashboard overview payload unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: The dashboard Accounts section requirements will define a compact availability summary derived from the existing overview accounts array.

## Impact

- Frontend dashboard components in `frontend/src/features/dashboard/components/`
- Frontend dashboard page integration in `frontend/src/features/dashboard/components/dashboard-page.tsx`
- Frontend dashboard tests in `frontend/src/features/dashboard/components/*.test.tsx`
