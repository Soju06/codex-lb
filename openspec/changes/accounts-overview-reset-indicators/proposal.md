## Why

The Accounts page list only shows the weekly quota bar today, so operators cannot quickly see the 5h window or the remaining time until reset from the overview. When many accounts are exhausted, the current ordering also makes it harder to spot which one will recover first.

## What Changes

- Show both 5h and weekly quota rows in the compact account list when the account has a 5h window.
- Display time-to-reset text for each visible quota row in the list.
- Sort the account list by the next upcoming quota reset, with accounts missing reset timestamps placed last.
- Add an appearance setting to choose whether compact account views show the 5h row, the weekly row, or both, with Both as the default.

## Impact

- Code: `frontend/src/features/accounts/components/account-list-item.tsx`, `frontend/src/features/accounts/components/account-list.tsx`, related tests
- Specs: `openspec/specs/frontend-architecture/spec.md`
