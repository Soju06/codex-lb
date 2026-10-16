# Proposal

## Why

Operators need an at-a-glance account inventory by plan and status, including accounts that are deactivated or require re-authentication. The Accounts page currently requires inspecting or filtering individual rows to obtain this overview.

## What Changes

- Add Dashboard-style donut cards for account counts by plan and by status above the account management views.
- Show total accounts, category counts and percentages, with localized labels, responsive layout and empty states.
- Derive statistics from the existing complete account list, independently of list filters and pagination.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Accounts page inventory distribution charts.

## Impact

Frontend Accounts page, shared donut presentation, translations, regression coverage and screenshots. No API, database, dependency or configuration additions.
