# Tasks

- [x] 1.1 Confirm no reader or writer of `prewarm_canary_bucket` /
      `prewarm_eligible_reason` remains in `app/`, `tests/`, `frontend/`,
      `scripts/`, `deploy/`.
- [x] 1.2 Remove both attributes from `RequestLog` in `app/db/models.py`.
- [x] 1.3 Add both columns to `_LEGACY_EXTRA_COLUMNS` so the schema-drift gate
      tolerates the retained physical columns.
- [x] 1.4 Cover the mixed-version contract in tests: head schema still carries
      both columns, the ORM does not map them, a legacy-style INSERT with
      explicit NULLs succeeds, and `check_schema_drift` is clean.
- [x] 1.5 Update the observability context and re-queue the drop revision
      (plus allow-list removal) as next-release queue item 1 in the
      deployment-installation context.
