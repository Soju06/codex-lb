## 1. Reproduce and repair

- [x] 1.1 Reproduce live ingestion followed by a freshness-skipped scheduler tick through public lifecycle methods; observe the missing send.
- [x] 1.2 Recover current reset evidence and reuse atomic claims; make the regression pass across scheduler restart.
- [x] 1.3 Cover duplicate snapshots, later polling, current eligibility and incomplete history through scheduler/ingestion integration tests.
- [x] 1.4 Reproduce and fix late-restart cutoff loss and non-reset trigger admission; verify their scheduler regressions pass.

## 2. Verify and deliver

- [x] 2.1 Run affected warm-up, ingestion and scheduler checks, lint, typing and OpenSpec validation with a dedicated disposable database.
- [x] 2.2 Review the fixed base/candidate diff and verify requirement coverage.
