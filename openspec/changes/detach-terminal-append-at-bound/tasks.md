## 1. Implementation

- [x] 1.1 Stop cancelling the terminal append task when the bound expires or the caller is cancelled.

## 2. Verification

- [x] 2.1 Reproduce the leaked writer slot on unchanged `main`: a terminal append that holds a real aiosqlite write transaction past the bound leaves `BEGIN IMMEDIATE` failing with `database is locked`.
- [x] 2.2 Cover both paths with the fix: on bound expiry, the caller gets the settlement result within the bound; on caller cancellation, the cancellation propagates; in both paths the append commits and a second connection takes `BEGIN IMMEDIATE`.
- [x] 2.3 Update batcher tests that asserted cancellation at the bound.
