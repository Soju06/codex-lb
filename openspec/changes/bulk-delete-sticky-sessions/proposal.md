## Why

The sticky-session administration table supports deleting one mapping at a time. Operators with dozens or hundreds of durable mappings must currently click `Remove` for each row individually, which is slow and error-prone.

Bulk session deletion should be available directly from the table so operators can clean up multiple mappings efficiently while still confirming destructive actions and seeing any partial failures.

## What Changes

- Add dashboard support for selecting multiple sticky-session rows, including `select all on current page`.
- Add a bulk `Delete Sessions` action with confirmation and selected-count messaging.
- Add backend support for best-effort bulk deletion with per-row failure reporting.
- Preserve current filters and pagination when the table refreshes after bulk deletion.

## Impact

- Specs: `openspec/specs/sticky-session-operations/spec.md`, `openspec/specs/frontend-architecture/spec.md`
- Backend: sticky-session admin API/service/repository
- Frontend: sticky-session table selection state, bulk action controls, confirmation UX, refresh behavior
- Tests: backend bulk-delete API coverage and frontend interaction coverage
