## Why

The sticky-session administration table supports deleting one mapping at a time. Operators with dozens or hundreds of durable mappings must currently click `Remove` for each row individually, which is slow and error-prone.

Bulk session deletion should be available directly from the table so operators can clean up multiple mappings efficiently while still confirming destructive actions and seeing any partial failures.

## What Changes

- Add dashboard support for selecting multiple sticky-session rows, including `select all on current page`.
- Add a bulk `Delete Sessions` action with confirmation and selected-count messaging.
- Add backend support for best-effort bulk deletion with per-row failure reporting.
- Preserve current filters and pagination when the table refreshes after bulk deletion.
- Add server-backed sticky-session list filters for account search and sticky-key search so operators can narrow deletion targets before bulk selection.
- Add server-backed sticky-session sort controls so operators can order rows by the most useful cleanup dimension before selecting targets.
- Add a filtered bulk-delete action so operators can delete the entire current filtered result set without selecting rows page by page.

## Impact

- Specs: `openspec/specs/sticky-session-operations/spec.md`, `openspec/specs/frontend-architecture/spec.md`
- Backend: sticky-session admin API/service/repository
- Frontend: sticky-session table selection state, filter and sort controls, filtered bulk-delete controls, confirmation UX, refresh behavior
- Tests: backend sticky-session list/delete query coverage plus frontend filter/sort and bulk-delete interaction coverage
