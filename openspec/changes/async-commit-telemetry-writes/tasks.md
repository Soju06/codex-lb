# Tasks

## 1. Shared helper

- [x] 1.1 Add `relax_commit_durability(session)` to `app/db/session.py`: PostgreSQL-only `SET LOCAL synchronous_commit = off` executed through the session (autobegin guarantees an open transaction), no-op for every other dialect.

## 2. Telemetry write call sites

- [x] 2.1 `RequestLogsRepository.add_log`: relax as the first statement of the insert transaction.
- [x] 2.2 `ApiKeysRepository.create_usage_reservation` and `ApiKeysRepository.settle_usage_reservation`: relax the reservation-creation and settlement transactions (covers finalize/fail/release plus the `last_used_at` touch riding the settlement commit).
- [x] 2.2b `ApiKeysRepository.release_stale_usage_reservations`: relax each stale-release batch transaction (scheduler path settles the same accounting rows as the request-path release).
- [x] 2.3 `UsageRepository.add_entry`, `UsageRepository.add_account_snapshot`, `AdditionalUsageRepository.add_entry`: relax the usage-history append transactions.
- [x] 2.4 Confirm no configuration write path (account/key/limit management, schedulers, migrations) calls the helper.

## 3. Verification

- [x] 3.1 Unit test: helper executes `SET LOCAL` only for PostgreSQL sessions and is a no-op for SQLite.
- [x] 3.2 PostgreSQL integration tests: `SHOW synchronous_commit` inside the relaxed transaction reports `off`; after COMMIT and after ROLLBACK the session default (`on`) is restored; `SET LOCAL` outside a transaction (autocommit) does not stick (the WARNING trap); telemetry write paths (including the scheduler's stale-reservation release) emit the relaxation and a configuration write does not.
- [x] 3.3 Register the integration test module in `POSTGRES_PYTEST_TARGETS`; run ruff check, ruff format, ty, and the unit suite (SQLite) plus the new integration module against PostgreSQL.
