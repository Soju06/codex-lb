# Tasks

## 1. Coalescer and flusher

- [x] 1.1 `ApiKeyLastUsedCoalescer` in `app/modules/api_keys/last_used_coalescer.py`: `record(key_id, used_at)` keeps the per-key maximum, `flush()` swaps the pending map before writing so mid-flush records land in the next interval, failed flushes merge the batch back (newer in-flight records win). Module singleton accessor.
- [x] 1.2 Flush implementation: one transaction per flush, one guarded UPDATE per touched key (`WHERE last_used_at IS NULL OR last_used_at < :new`) under `sqlite_writer_section()`, background session.
- [x] 1.3 `ApiKeyLastUsedFlushScheduler` (constant 30 s interval, replica-local, NOT leader-gated, mirrors the reset-credits scheduler start/stop shape); `stop()` performs the final flush.

## 2. Write-path switch

- [x] 2.1 `_settle_usage_reservation` records into the coalescer after the settlement commit instead of `update_last_used(commit=False)`.
- [x] 2.2 `record_usage`/`increment_limit_usage` stop writing `last_used_at` inline and record through the coalescer; remove the now-unused `ApiKeysRepository.update_last_used` and its protocol entry.
- [x] 2.3 Wire the scheduler into `app/main.py` lifespan: start with the other schedulers, stop (with final flush) after proxy settlement tasks drain.

## 3. Review follow-up: lossless shutdown flush (codex P2 x2)

- [x] 3.1 Shutdown-path flush hardening: `flush_with_retries()` on the coalescer retries transient failures (3 attempts, 0.5 s constant backoff); after exhaustion it logs the pending API key ids and timestamps at WARNING and returns without raising. `stop()` uses it for the final flush.
- [x] 3.2 Ordering guarantee for late producers: `stop()` switches the coalescer to shutdown write-through mode BEFORE the final flush; afterwards `record()` (now async) immediately flushes with the same retry policy, so a settlement task that outlived `drain_persistence_tasks()` cannot park a touch that nothing will flush. `scheduler.start()` resets the mode. The settlement call site moved outside `sqlite_writer_section()` because a write-through flush takes the writer section itself.
- [x] 3.3 Regression tests: final flush retries a transient failure then succeeds (coalescer-level and via `scheduler.stop()`); exhausted retries log pending key ids + timestamps and do not raise; a record after `stop()` writes through to the DB; `start()` restores write-behind parking.
- [x] 3.4 Spec delta updated (write-through mode, bounded retries, WARNING dump scenarios).

## 4. Verification

- [x] 4.1 New unit tests: multiple records coalesce to one flush with the latest value winning; flush never regresses a newer stored `last_used_at` (greatest-wins); scheduler `stop()` flushes pending; flush failure retains pending for the next tick.
- [x] 4.2 Update existing `test_api_keys_service.py` last-used assertions to the coalescer contract (settlement commit no longer carries the UPDATE).
- [x] 4.3 `uv run ruff check .`, `uv run ruff format .`, `uv run ty check app`, full `uv run pytest tests/unit -q` (SQLite) plus the touched suites against PostgreSQL.
