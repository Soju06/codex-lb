## 1. Specify and reproduce

- [x] Identify the repeated guarded-update miss in the current repository implementation.
- [x] Record the bounded sweep contract and legacy timestamp limitation.
- [x] Add regressions proving skipped batches cannot block later expired limits.

## 2. Implement and verify

- [x] Advance the sweep cursor from the last selected primary key, including guarded misses.
- [x] Preserve successful-update counting, exact guards and per-batch commits.
- [x] Run focused API-key repository, scheduler and integration checks.
- [x] Synchronize canonical specifications/context and validate OpenSpec before archive.
