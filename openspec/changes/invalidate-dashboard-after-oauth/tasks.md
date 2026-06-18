## 1. OAuth cache invalidation

- [x] 1.1 Add a shared account-related query invalidation helper that does not import account hooks into OAuth tests.
- [x] 1.2 Invalidate account list, account trend, dashboard overview, and dashboard projection queries after successful OAuth completion.
- [x] 1.3 Cover manual browser callback, browser status success, and device completion success paths with frontend tests.

## 2. Validation

- [x] 2.1 Run the targeted OAuth/account hook tests.
- [x] 2.2 Run frontend lint and typecheck.
- [x] 2.3 Validate the OpenSpec change with `openspec validate invalidate-dashboard-after-oauth --strict`.
