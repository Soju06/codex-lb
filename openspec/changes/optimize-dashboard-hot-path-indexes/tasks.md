# Tasks

- [x] Add the `20260717_000000_optimize_dashboard_hot_path_indexes` Alembic migration (covering index, distinct-labels index, redundant-index drops, PostgreSQL autovacuum storage parameters).
- [x] Register `idx_logs_dash_usage_covering` and `ix_additional_usage_distinct_labels` in ORM metadata and the manual drift index requirements; remove the dropped indexes from both.
- [x] Add regression tests for drift detection of the new indexes, idempotent migration application after a live hotfix, and removal of the redundant indexes at head.
- [x] Validate migration and drift checks locally (`tests/unit/test_db_migrate.py`).
- [x] Run OpenSpec validation (`openspec validate --specs`).
