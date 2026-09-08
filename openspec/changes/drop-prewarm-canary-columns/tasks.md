- [x] 1.1 Confirm no reader or writer of `prewarm_canary_bucket` /
      `prewarm_eligible_reason` remains in `app/`, `tests/`, `frontend/`,
      `scripts/`, `deploy/`.
- [x] 1.2 Remove both attributes from `RequestLog` in `app/db/models.py`.
- [x] 1.3 Add `20260908_000000_drop_prewarm_canary_columns` with an idempotent
      batch-mode drop and a nullable re-add downgrade.
- [x] 1.4 Cover upgrade (row survival, no-op re-run) and downgrade in
      `tests/integration/test_migrations.py`.
- [x] 1.5 Update the observability spec / context and retire item 1 of the
      deployment-installation next-release queue.
