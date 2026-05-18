## 1. Spec

- [x] 1.1 Extend the database-migrations spec with a startup schema drift guard requirement
- [x] 1.2 Sync database-migrations context with the new startup drift verification step
- [x] 1.3 Validate OpenSpec specs

## 2. Tests

- [x] 2.1 Add a unit test proving `init_db()` fails fast on post-migration schema drift when fail-fast is enabled
- [x] 2.2 Add a unit test proving `init_db()` logs explicit drift details and continues when fail-fast is disabled

## 3. Implementation

- [x] 3.1 Run `check_schema_drift()` after successful startup migrations
- [x] 3.2 Surface a clear runtime error message for startup drift
- [x] 3.3 Reuse the existing `database_migrations_fail_fast` gate instead of introducing fallback behavior
