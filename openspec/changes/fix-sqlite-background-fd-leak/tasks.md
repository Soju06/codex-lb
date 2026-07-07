## 1. Implementation

- [x] 1.1 Configure file-backed SQLite main/background async engines with `NullPool`.
- [x] 1.2 Add a shared rollback + shielded close helper for manually-created sessions.
- [x] 1.3 Replace manual session close paths with the shared helper.
- [x] 1.4 Refactor usage, model-registry, and reset-credits background refreshes so upstream network I/O does not hold the account/usage read session.

## 2. Tests

- [x] 2.1 Cover SQLite file-engine kwargs and PostgreSQL pool-control preservation.
- [x] 2.2 Cover rollback-before-close manual session cleanup.
- [x] 2.3 Cover usage/model/reset-credits refresh session closure before upstream fetch.
- [x] 2.4 Cover usage refresh cancellation closing the read session.

## 3. Validation

- [x] 3.1 Run focused unit tests.
- [x] 3.2 Run ruff checks.
- [x] 3.3 Validate OpenSpec specs.
