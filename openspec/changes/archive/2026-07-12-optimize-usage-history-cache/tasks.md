## 1. Usage history cache

- [x] 1.1 Remove full-window SQLite fingerprinting from bulk history cache hits.
- [x] 1.2 Append only new rows with IDs greater than the cached max ID.
- [x] 1.3 Clear the cache when account merge/delete paths mutate usage-history ownership.

## 2. Additional usage latest reads

- [x] 2.1 Add a SQLite fast path for latest additional-usage row per account.
- [x] 2.2 Preserve canonical quota-key and alias matching semantics.

## 3. Verification

- [x] 3.1 Add/update integration tests for bulk-history cache append and explicit invalidation.
- [x] 3.2 Add SQLite SQL-shape coverage for additional latest reads.
- [x] 3.3 Run focused repository tests and static checks.
