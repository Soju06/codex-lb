# Tasks

## 1. Schema and migration

- [x] 1.1 Declare `RequestConversationHourlyRollup` and the `conversation_folded_through` state column in `app/db/models.py`.
- [x] 1.2 Guarded DDL-only revision `20260806_010000_add_conversation_presence_rollup` (create table, add state column with epoch server default, guarded downgrade).
- [x] 1.3 Migration round-trip test (upgrade → schema/PK/epoch-backfill assertions → downgrade → re-upgrade), registered in `POSTGRES_PYTEST_TARGETS`.

## 2. Fold pass and lifecycle

- [x] 2.1 `run_conversation_fold_pass` in `usage_time_rollup.py`: own watermark, shared state-row lock, DELETE-then-INSERT slices, fold-filtered empty-prefix jump, shared `conversation_id_expr` (also adopted by both repositories' `_conversation_id_expr`).
- [x] 2.2 Third leg in `AccountUsageRollupScheduler._fold_as_leader` (own try-block).
- [x] 2.3 Satellite registered in `_ROLLUP_TABLES`: soft-delete re-key, hard-delete removal, and consolidation re-key mirrors work unchanged; `update_model_for_request` needs no conversation bound (documented).
- [x] 2.4 Retention prune gate takes the min over all three watermarks.

## 3. Read switch

- [x] 3.1 Single-statement presence-union primitives in `usage_time_rollup_read.py` (`conversation_presence_union`, `conversation_labeled_presence_union`), state row joined into both branches, exact complement clause, full-raw degrade on epoch/missing watermark.
- [x] 3.2 Switch `aggregate_conversations_by_bucket` (hour-multiple buckets; others degrade full-raw) and `_aggregate_activity`'s conversation metrics.
- [x] 3.3 Switch reports `aggregate_summary` and `aggregate_daily_rows` per-day conversation counts (unfiltered calls only; filtered calls keep the legacy statement).

## 4. Verification

- [x] 4.1 Parity harness: conversation bucket series (1h/6h/non-multiple degrade) and reports summary/daily (UTC and +05:30 local days) snapshotted; conversation folds run at every watermark state, in the concurrent-fold injection, the concurrent-pass gather, and the escape hatch (satellite truncated + watermark reset).
- [x] 4.2 Retention parity expectations reversed: conversation activity metrics and hour-aligned conversation buckets now SURVIVE raw pruning; reports summary conversation count survives; daily day-row membership stays raw-driven (documented).
- [x] 4.3 New tests: fold-boundary dedup + idempotent fixed point, bucket-series parity and non-hour-multiple degrade, soft-delete mirror (dashboard drops / reports keep), hard-delete mirror (shared conversation keeps the survivor), retention gate paused until the conversation backfill catches up.
- [x] 4.4 SQLite + PostgreSQL runs of the rollup/parity/retention/migration suites; ruff, ruff format, ty.
