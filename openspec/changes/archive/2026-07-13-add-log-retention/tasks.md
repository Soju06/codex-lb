## 1. Settings

- [x] 1.1 Add `request_log_retention_days` / `usage_history_retention_days` to `app/core/config/settings.py` (default 0 = disabled) with a validator enforcing the 30/45-day floors for non-zero values

## 2. Retention job

- [x] 2.1 Implement `app/core/retention/` pass: request_logs pruning with `min(cutoff, rollup watermark)` gate (skip when no state row), usage_history/additional_usage_history pruning excluding latest row per identity key, all via bounded id-subquery delete batches with per-batch commits; clear the SQLite bulk-history cache after usage pruning
- [x] 2.2 Add `build_data_retention_scheduler()` (hourly, leader-gated, existing scheduler pattern); wire start/stop in `app/main.py`; disable via autouse fixture in `tests/conftest.py`

## 3. Tests

- [x] 3.1 Settings validation: 0 accepted, sub-floor rejected, floor accepted
- [x] 3.2 Request-log pruning: unfolded rows survive; folded old rows deleted; summaries unchanged before/after pruning (product path `GET /api/accounts`); skipped when no state row
- [x] 3.3 Usage-history pruning: latest row per (account, window) and per (account, quota_key, window) survives; older rows deleted; disabled default deletes nothing
- [x] 3.4 Batch loop: backlog larger than one batch fully drains across multiple transactions
- [x] 3.5 Add suite to `POSTGRES_PYTEST_TARGETS`

## 4. Validation & docs

- [x] 4.1 Create `openspec/specs/data-retention/context.md` (operator contract, floors rationale, first-prune behavior, previous_response_id caveat)
- [x] 4.2 `openspec validate --specs`, `ruff`, `ty`, pytest on SQLite + PostgreSQL
