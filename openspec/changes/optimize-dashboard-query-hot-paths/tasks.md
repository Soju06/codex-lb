## 1. Spec and schema

- [x] 1.1 Add query-caching requirements for avoiding unbounded window-ranking scans on selector/dashboard hot paths.
- [x] 1.2 Add an idempotent Alembic migration for the additional-usage latest-row and request-log aggregate indexes.
- [x] 1.3 Add matching ORM index metadata.

## 2. Query changes

- [x] 2.1 Update additional-quota latest-by-account lookup to avoid `row_number()` and use canonical hot-path matching.
- [x] 2.2 Update account request usage summaries to dedupe duplicate request-log rows with grouped latest IDs before aggregation.

## 3. Validation

- [x] 3.1 Add integration regression coverage that verifies the hot-path emitted SQL does not use window ranking and preserves summary/latest-row semantics.
- [x] 3.2 Run targeted pytest for dashboard/query OOM regressions.
- [x] 3.3 Run ruff check, ruff format check, and ty check.
- [ ] 3.4 Validate the OpenSpec change when the OpenSpec CLI is available.
