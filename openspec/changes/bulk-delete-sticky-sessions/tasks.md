## 1. Spec

- [ ] 1.1 Add sticky-session bulk deletion requirements
- [ ] 1.2 Add dashboard bulk-selection and delete interaction requirements

## 2. Backend

- [ ] 2.1 Add a bulk sticky-session delete API that accepts multiple `(key, kind)` identifiers
- [ ] 2.2 Implement best-effort deletion with success and failure reporting

## 3. Frontend

- [ ] 3.1 Add row selection and current-page select-all behavior to the sticky-session table
- [ ] 3.2 Add bulk delete action and confirmation dialog
- [ ] 3.3 Refresh the table while preserving filters and pagination context after bulk deletion

## 4. Tests

- [ ] 4.1 Add backend tests for bulk sticky-session deletion and partial-failure reporting
- [ ] 4.2 Add frontend tests for selection, confirmation, and post-delete refresh behavior
