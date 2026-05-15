## 1. Spec

- [x] 1.1 Add frontend-architecture requirements for hidden soft-deleted request-log rows and unchanged overview metrics.
- [x] 1.2 Add database-migrations requirements for the non-cascading request-log FK and soft-delete column/index.
- [x] 1.3 Validate OpenSpec changes.

## 2. Implementation

- [x] 2.1 Add nullable `request_logs.deleted_at` storage and replace the account FK cascade path with `ON DELETE SET NULL`.
- [x] 2.2 Update account deletion to soft-delete matching request-log rows instead of removing them.
- [x] 2.3 Exclude soft-deleted rows from the dashboard request-log list and filter options only.
- [x] 2.4 Add or adjust indexes needed for the new dashboard request-log filter path.

## 3. Validation

- [x] 3.1 Add backend coverage for account deletion preserving request-log metrics while hiding deleted rows from `/api/request-logs`.
- [x] 3.2 Add migration coverage for the request-log soft-delete column, index, and `SET NULL` FK behavior.
- [x] 3.3 Run targeted backend validation plus `openspec validate --specs`.
