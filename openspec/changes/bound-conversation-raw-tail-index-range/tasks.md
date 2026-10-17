# Tasks

## 1. Implementation

- [x] 1.1 `usage_time_rollup_read.py`: read the conversation watermark as scalar subqueries of the state row (`_conversation_watermark_subquery`, `_conversation_watermark_epoch_subquery`); drop the state-row joins from `_conversation_folded_select` and `_conversation_raw_select`.
- [x] 1.2 `_conversation_tail_start` / `_conversation_raw_complement_clause`: the union emits `requested_at < lo OR requested_at >= greatest(lo, least(coalesce(W, lo), hi))` over constant bounds (dialect-split `greatest`/`least` vs SQLite two-argument `max`/`min`); NULL or epoch watermark degrades to the caller's full window.
- [x] 1.3 Delete `conversation_labeled_presence_union`: uncalled since the reports reads moved to the permanent report aggregates (`report-aggregation`).
- [x] 1.4 Update the module docstring and the conversation-primitives comment block to describe the scalar-subquery mechanism and why it keeps one snapshot.

## 2. Verification

- [x] 2.1 New `tests/integration/test_conversation_presence_union.py`: raw-only oracle parity for state-row-missing / epoch / before / inside / after watermark positions over aligned, unaligned and open-ended windows (with display buckets and soft-deleted rows); statement-shape capture of the two dashboard reads (scalar subqueries, no state-row join, no `IS NULL` watermark disjunct); PostgreSQL-only `EXPLAIN (ANALYZE)` of the bind-parameter statement (compiled `EXPLAIN` wrapper, `SET LOCAL` scoped GUC) asserting index-bounded `request_logs` nodes with exact raw-complement row counts for the watermark missing / below / at-lo / inside / at-hi / past-hi / fully-folded layouts (skips on SQLite).
- [x] 2.2 Append the new module to `POSTGRES_PYTEST_TARGETS` in the Makefile.
- [x] 2.3 Existing oracles green on SQLite and PostgreSQL 16: `tests/integration/test_request_usage_rollup_parity.py`, `tests/integration/test_dashboard_overview.py`, `tests/integration/test_request_usage_time_rollup.py`, reports/request-log repository unit tests.
- [x] 2.4 `uv run ruff check .`, `uv run ruff format --check .`, proxy architecture / cancellation-safety / timing-seam / settings-tier guards, `uv run ty check`, `openspec validate bound-conversation-raw-tail-index-range --strict`.
