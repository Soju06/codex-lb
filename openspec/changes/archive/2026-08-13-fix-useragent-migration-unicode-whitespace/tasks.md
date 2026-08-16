## 1. Runtime grouping

- [x] 1.1 Update request-log user-agent grouping so slash-delimited families retain the complete pre-slash prefix while slash-free behavior remains unchanged.
- [x] 1.2 Add focused unit coverage for `Codex Desktop/...` and preserve existing null, blank, and slash-free cases.

## 2. Historical data migration

- [x] 2.1 Add an Alembic revision after the current head that backfills `useragent_group` from the unprocessed pre-slash substring only for non-null `useragent` rows containing `/`.
- [x] 2.2 Verify migration behavior preserves null and slash-free rows and does not trim stored values.

## 3. Validation

- [x] 3.3 Add focused PostgreSQL migration-expression coverage.
- [x] 3.1 Run the focused proxy user-agent tests and Alembic head/upgrade validation.
- [x] 3.2 Run `openspec validate --specs` and mark completed tasks.
