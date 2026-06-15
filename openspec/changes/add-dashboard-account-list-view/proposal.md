## Why

Operators with many accounts need a denser way to scan account status, quota, credits, and actions from the dashboard. The existing account cards remain useful, but they take more vertical space when the account count grows.

## What Changes

- Add a dashboard Accounts section view-mode control with card and list options.
- Keep the current card view as the default.
- Persist the selected dashboard account view mode locally.
- Add a compact list view that exposes the same dashboard account actions as the card view.
- Render compact quota meters in the list view so remaining capacity is visually scannable.
- Allow operators to sort the list by clicking account, status, plan, quota, credits, and warm-up headers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: The dashboard Accounts section can render account summaries as cards or as a compact list, based on a local operator preference.

## Impact

- Dashboard account section components in `frontend/src/features/dashboard/components/`
- Dashboard local preferences in `frontend/src/hooks/use-dashboard-preferences.ts`
- Frontend dashboard component and preference tests
