## Why

Operators need an overview of account health, quota, and subscription time without opening each account. Token expiry and quota reset times currently provide no reliable indication of the remaining subscription term.

## What Changes

- Add a locally remembered grid view alongside the existing Accounts list and detail layout, sharing search, filters, sorting, and account selection.
- Display subscription time remaining and its recorded end date when explicitly present in stored subscription metadata; otherwise show an unknown state, distinct from token expiry and quota resets.
- Show identity, status, plan, quota, usage totals, token status, routing, warm-up, and credits on overview cards, with access to existing management details.

## Capabilities

### New Capabilities
- `account-subscription-term`: Safely expose explicit subscription-term metadata from stored credentials, with unknown and elapsed states.

### Modified Capabilities
- `frontend-architecture`: Add an optional responsive Accounts grid without losing existing account management behavior.

## Impact

Account summary API mapping and schemas, Accounts React components and translations, regression tests and browser screenshots. No new upstream requests, database migration, settings, or dependencies.
