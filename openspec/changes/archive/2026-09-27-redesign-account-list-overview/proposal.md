## Why

The Accounts grid now presents a complete account overview, but the List view still shows the older compact sidebar rows. Operators need the same account information in a scan-friendly dense list so the two modes are equivalent and useful at different densities.

## What Changes

- Redesign Accounts List view as a responsive dense account table/list with identity, plan/status, subscription, quota/reset, token, usage/credits and management entry points.
- Replace the list/detail sidebar with a full-width overview, shared detail dialog and pagination at 24 accounts per page.
- Keep existing search, status filter, sort, selection, privacy masking, read-only behavior and details dialog semantics.
- Add list-view screenshot and regression coverage for the full information surface and mobile overflow.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `frontend-architecture`: Require the Accounts List view to present a full account overview equivalent to the Grid view.

## Impact

Frontend Accounts components, translations, tests and screenshots. No API, schema, database or dependency changes.
