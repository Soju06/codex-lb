# Tasks

## 1. Specification & Artifacts

- [x] 1.1 Create `openspec/changes/cap-sqlite-bulk-usage-history/proposal.md` documenting problem statement, memory impact, and proposed changes.
- [x] 1.2 Create `openspec/changes/cap-sqlite-bulk-usage-history/design.md` detailing SQLite index analysis, alternatives evaluated, query shapes, and in-memory fallback.
- [x] 1.3 Create `openspec/changes/cap-sqlite-bulk-usage-history/tasks.md` implementation checklist.
- [x] 1.4 Create `openspec/changes/cap-sqlite-bulk-usage-history/specs/query-caching/spec.md` delta spec extending per-account bounded reads to SQLite and adding SQLite index plan scenario.
- [x] 1.5 Validate OpenSpec change artifacts via `@fission-ai/openspec validate`.

## 2. Code Implementation

- [x] 2.1 In `app/modules/usage/repository.py`, implement `_bulk_history_since_capped_sqlite`:
  - Connect with `detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES`, `PRAGMA query_only=ON`, and `PRAGMA busy_timeout=30000`.
  - Differentiate window predicate between `window == "primary"` (`coalesce(window, 'primary') = 'primary'`) and other windows (`window = ?`).
  - For each account, compute `cutoff = max(cutoffs.get(account_id, since), since) if cutoffs else since`.
  - When `uncapped_recent_floor is None`, execute backward seek with `LIMIT per_account_row_cap`.
  - When `uncapped_recent_floor is not None`, derive `uncapped_floor = max(cutoff, uncapped_recent_floor)`; execute all-recent forward query if `uncapped_floor <= cutoff`, otherwise execute `UNION ALL` of capped tail `[cutoff, uncapped_floor)` (`LIMIT per_account_row_cap`) and uncapped recent `>= uncapped_floor`.
  - Hydrate `UsageHistorySnapshot` positional tuples and sort each account's slice oldest-first `(snapshot.recorded_at, snapshot.id)`.
- [x] 2.2 In `UsageRepository.bulk_history_since`:
  - Route capped requests on file-backed SQLite (`per_account_row_cap is not None and sqlite_path is not None`) to `_bulk_history_since_capped_sqlite` via `to_thread.run_sync`.
  - Handle in-memory session fallback (`sqlite_path is None`) when `per_account_row_cap is not None` by slicing `snapshots[-per_account_row_cap:]` or `tail[-per_account_row_cap:] + recent`.
  - Preserve uncapped `_bulk_history_since_sqlite` path untouched when `per_account_row_cap is None`.
  - Update method docstrings.

## 3. Verification & Testing

- [x] 3.1 In `tests/integration/test_usage_repository.py`:
  - Update `test_bulk_history_since_per_account_row_cap_keeps_newest_rows` to assert `[15.0, 16.0, 17.0]` for `acc-dense` on both PostgreSQL and SQLite.
  - Remove dialect skip from `test_bulk_history_since_row_cap_respects_per_account_cutoffs_postgresql`.
  - Remove dialect skip from `test_bulk_history_since_row_cap_exempts_uncapped_recent_floor_postgresql`.
  - Add `test_bulk_history_since_capped_query_plan_is_indexed_sqlite` asserting index utilization on `idx_usage_window_*` without `USE TEMP B-TREE`.
- [x] 3.2 In `tests/integration/test_dashboard_overview.py`:
  - Update `test_dashboard_projections_ewma_tail_cap_matches_uncapped_history` to assert `capped_rows == 18 + 64` across both PostgreSQL and SQLite.
- [x] 3.3 Run test suite: `PYTHONFAULTHANDLER=1 uv run pytest tests/integration/test_usage_repository.py -k "test_bulk_history_since"`.
- [x] 3.4 Run linting and type checking: `uv run ruff check .`, `uv run ruff format --check .`, and `uv run ty check`.
- [x] 3.5 Author summary report in `/tmp/work/proposer-report.md`.
