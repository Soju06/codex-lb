# Tasks

- [x] Add the `20260717_000000_optimize_dashboard_hot_path_indexes` Alembic migration (covering index, distinct-labels index, redundant-index drops, PostgreSQL autovacuum storage parameters).
- [x] Register `idx_logs_dash_usage_covering` and `ix_additional_usage_distinct_labels` in ORM metadata and the manual drift index requirements; remove the dropped indexes from both.
- [x] Add regression tests for drift detection of the new indexes, idempotent migration application after a live hotfix, and removal of the redundant indexes at head.
- [x] Validate migration and drift checks locally (`tests/unit/test_db_migrate.py`).
- [x] Run OpenSpec validation (`openspec validate --specs`).
- [x] Keep `idx_logs_request_status_api_key_time` (sessionless response-owner fallback needs its ordered retrieval) and restore its ORM definition.
- [x] Repair invalid leftovers from interrupted concurrent builds (`pg_index.indisvalid`) and drop redundant PostgreSQL indexes with `DROP INDEX CONCURRENTLY`.
- [x] Modify the owning `api-keys` composite-index requirement via this change's delta (filter phase served by `idx_logs_api_key_time`; grouped column heap-fetched).
- [x] Re-parent the migration onto `20260717_000000_merge_retention_and_reset_credit_display_heads` after #1358 merged.
