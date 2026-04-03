## 1. Spec

- [x] 1.1 Add sticky-session bulk deletion requirements
- [x] 1.2 Add dashboard bulk-selection and delete interaction requirements

## 2. Backend

- [x] 2.1 Add a bulk sticky-session delete API that accepts multiple `(key, kind)` identifiers
- [x] 2.2 Implement best-effort deletion with success and failure reporting
- [x] 2.3 Add sticky-session list filtering by account search and sticky-key search
- [x] 2.4 Add sticky-session list sorting options for common cleanup workflows
- [x] 2.5 Add filtered sticky-session bulk deletion using the active list query

## 3. Frontend

- [x] 3.1 Add row selection and current-page select-all behavior to the sticky-session table
- [x] 3.2 Add bulk delete action and confirmation dialog
- [x] 3.3 Refresh the table while preserving filters and pagination context after bulk deletion
- [x] 3.4 Add sticky-session filter controls for account search and sticky-key search
- [x] 3.5 Add sticky-session sort controls in the table toolbar
- [x] 3.6 Add a delete-filtered action and confirmation flow for the current filtered result set

## 4. Tests

- [x] 4.1 Add backend tests for bulk sticky-session deletion and partial-failure reporting
- [x] 4.2 Add frontend tests for selection, confirmation, and post-delete refresh behavior
- [x] 4.3 Add backend and frontend regression coverage for sticky-session filtering
- [x] 4.4 Add backend and frontend regression coverage for sticky-session sorting
- [x] 4.5 Add backend and frontend regression coverage for filtered bulk deletion
