# Tasks

## 1. Coalescer and flusher

- [x] 1.1 `ApiKeyLastUsedCoalescer` in `app/modules/api_keys/last_used_coalescer.py`: `record(key_id, used_at)` keeps the per-key maximum, `flush()` swaps the pending map before writing so mid-flush records land in the next interval, failed flushes merge the batch back (newer in-flight records win). Module singleton accessor.
- [x] 1.2 Flush implementation: one transaction per flush, one guarded UPDATE per touched key (`WHERE last_used_at IS NULL OR last_used_at < :new`) under `sqlite_writer_section()`, background session.
- [x] 1.3 `ApiKeyLastUsedFlushScheduler` (constant 30 s interval, replica-local, NOT leader-gated, mirrors the reset-credits scheduler start/stop shape); `stop()` performs the final flush.

## 2. Write-path switch

- [x] 2.1 `_settle_usage_reservation` records into the coalescer after the settlement commit instead of `update_last_used(commit=False)`.
- [x] 2.2 `record_usage`/`increment_limit_usage` stop writing `last_used_at` inline and record through the coalescer; remove the now-unused `ApiKeysRepository.update_last_used` and its protocol entry.
- [x] 2.3 Wire the scheduler into `app/main.py` lifespan: start with the other schedulers, stop (with final flush) after proxy settlement tasks drain.

## 3. Verification

- [x] 3.1 New unit tests: multiple records coalesce to one flush with the latest value winning; flush never regresses a newer stored `last_used_at` (greatest-wins); scheduler `stop()` flushes pending; flush failure retains pending for the next tick.
- [x] 3.2 Update existing `test_api_keys_service.py` last-used assertions to the coalescer contract (settlement commit no longer carries the UPDATE).
- [x] 3.3 `uv run ruff check .`, `uv run ruff format .`, `uv run ty check app`, full `uv run pytest tests/unit -q` (SQLite) plus the touched suites against PostgreSQL.
