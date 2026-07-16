## Context

`AccountsRepository.list_request_usage_summary_by_account` (`app/modules/accounts/repository.py:82-142`) powers the per-account lifetime usage block on `GET /api/accounts`. To collapse duplicate log rows (issue #841, PR #904) it groups the **entire** `request_logs` table by `(account_id, request_id, requested_at)` — a nearly unique key — takes `max(id)` per group, and joins the table back to itself before summing. The `query-caching` spec already forbids `row_number()` window ranking here (PR #1107), but the grouped-latest-id shape still scans all history on every accounts-page load and degrades monotonically as logs accumulate. It also couples lifetime totals to raw log retention: rows can never be pruned without corrupting the accounts page.

Constraints:

- Dedupe semantics from #904 must be preserved exactly: only the `max(id)` row per `(account_id, request_id, requested_at)` counts; warmup/limit_warmup kinds and `deleted_at IS NOT NULL` rows are excluded.
- Duplicate rows share an **identical** `requested_at` by definition of the dedupe key.
- Log rows are mutated after insert in one known path: `rewrite_request_log_model` retries for ≤1.55 s after stream end (`app/modules/proxy/_service/request_log.py:74-98`). Cost/model corrections therefore settle within seconds of `requested_at`.
- Bulk row removal happens only on account deletion (`AccountsRepository.delete`): hard delete with `delete_history=True`, otherwise soft delete that also sets `account_id = NULL` (which already removes those rows from per-account summaries).
- Both SQLite and PostgreSQL backends must behave identically.
- Multiple instances may share one database; background schedulers use `app/core/scheduling/leader_election.py`.

## Goals / Non-Goals

**Goals:**

- `GET /api/accounts` usage summaries stop scanning full `request_logs` history; per-read work is bounded by the fold cadence, not table size.
- Identical result semantics (totals, dedupe, filtering) to the current query.
- Rollup state survives restarts and makes future log pruning safe (folded totals persist after raw rows are deleted).
- Backfill happens in the background; the migration itself is instant.

**Non-Goals:**

- No log retention/pruning in this change (separate change builds on it).
- No change to windowed aggregates (dashboard overview, reports, usage service).
- No new operator-facing settings unless unavoidable; cadence/lag are internal constants.
- No external API shape change.

## Decisions

### D1. Persistent rollup + bounded live tail, not a TTL cache and not a write-path counter

- A pure in-process TTL cache keeps the unbounded first-hit scan, does nothing for multi-instance deployments, and still blocks retention. Rejected.
- Incrementing counters inside `add_log` would need a duplicate-row lookup per write (hot-path SELECT) to preserve #904 dedupe, and would drift on the post-insert `rewrite_request_log_model` update. Rejected.
- Chosen: fold history into `account_usage_rollups` periodically; serve summaries as `rollup + live aggregate over requested_at > folded_through`. The tail is bounded by fold cadence and served by existing `idx_logs_account_id_requested_at`.

### D2. Single global watermark on a dedicated, migration-seeded state row

`folded_through` lives on a one-row table `account_usage_rollup_state` (id = 1), seeded by the migration to epoch. This was originally designed as a per-rollup-row column, which adversarial review killed twice over:

- An empty `account_usage_rollups` table gives `SELECT ... FOR UPDATE` nothing to lock, so two instances racing on the very first backfill (leader election is off by default) would both fold the same window and permanently double every account's totals. The seeded state row always exists, so concurrent fold passes serialize on its row lock and re-read the advanced watermark. The fold also self-bootstraps the row (`ON CONFLICT DO NOTHING`) for databases created via `metadata.create_all`.
- Reading sums and watermark as two statements loses a fold window under PostgreSQL READ COMMITTED (sums from before a fold commit, watermark from after ⇒ the just-folded slice is in neither). Reads therefore fetch sums + watermark in ONE statement (`state LEFT JOIN rollups`), which is a single snapshot.

A per-account watermark was rejected because it forces per-account lateral tail queries; the global watermark keeps the tail one grouped aggregate. The state row also persists watermark progress even when a fold window contains no foldable rows, so empty stretches of history are never rescanned.

### D3. Fold boundary is `requested_at`-based with a safety lag exceeding max request duration

Each fold pass locks the state row, aggregates rows in `(folded_through, now − LAG]` (sliced, see below) using the exact dedupe query shape, upsert-adds sums into rollup rows, and advances the state row — one transaction per slice.

- Because duplicates share an identical `requested_at`, a `requested_at` boundary can never split a dedupe group across folded/tail sides.
- `LAG = 24 hours` (constant). The lag MUST exceed the maximum distance between a row's `requested_at` (request START) and its insertion time (stream END): a long-running stream inserts a row dated its full duration in the past, and a row landing below an already-advanced watermark would be neither folded nor in the live tail — silently vanishing from totals. 24 h dwarfs any survivable stream duration and trivially covers the seconds-scale duplicate re-persist and `rewrite_request_log_model` paths. (Found by a regression test; originally 1 h.)
- `CADENCE = 15 minutes` (constant). Worst-case tail is ~24 h of logs per read — an indexed range scan of one day, not the full table.
- First pass after deploy starts from the seeded epoch watermark and performs the historical backfill in bounded slices (7-day windows, one transaction each). Until the first slice lands, the summary read degrades to exactly today's behavior (no rollup rows, tail = full history), so there is no correctness gap during backfill.
- If an account is deleted between a slice's aggregate and its upsert, the FK failure aborts only that slice; the watermark has not advanced, and the retry re-aggregates without the deleted account's rows. The fold also re-checks account existence inside the transaction to keep this window minimal.

### D4. Scheduler and leadership

New `build_account_usage_rollup_scheduler()` following the existing pattern (`app/modules/sticky_sessions/cleanup_scheduler.py` et al.), started/stopped in `app/main.py` lifespan, gated by `LeaderElection` so only one instance folds. Fold uses the background session factory (separate pool), never request sessions.

### D5. Lifecycle integration

`AccountsRepository.delete` deletes the account's rollup row in the same transaction (both `delete_history` variants — the soft-delete path NULLs `account_id` on logs, so folded sums must not survive either). No FK cascade reliance (SQLite FK pragma variability); explicit `DELETE` like the neighboring `StickySession` cleanup.

ChatGPT-identity duplicate consolidation (`_reconcile_chatgpt_identity_duplicates`) reassigns the duplicates' request logs to the canonical account and deletes the duplicate account rows. Folded usage must follow those logs: the consolidation now merges the duplicates' rollup sums into the canonical account's rollup row (upsert-add) and deletes the duplicates' rows in the same transaction, or the canonical account would silently lose all pre-watermark history from its lifetime totals (adversarial-review finding).

### D6. Summary read path

`list_request_usage_summary_by_account` becomes: read rollup sums + watermark in one statement (`state LEFT JOIN rollups`, see D2), run the existing dedupe aggregate with an added `requested_at > folded_through` predicate, merge in Python (int sums; `cached ≤ input` clamp applied after merge, preserving current post-aggregation clamp semantics). Same method signature and return type; callers unchanged.

## Risks / Trade-offs

- [Folded rows become immutable: a row inserted or mutated with `requested_at` more than 24 h in the past is not reflected] → the 24 h lag exceeds every known late-write path (max stream duration, duplicate re-persists, post-stream rewrites); the residual failure mode is bounded to a single request's tokens. Accepted.
- [Manual DB surgery on old `request_logs` rows no longer changes summaries] → documented in capability context; a fold-state reset (delete all rollup rows AND the state row in one transaction) forces a full re-backfill, providing an operator escape hatch. Deleting only one of the two is unsafe (lost folded totals or double-fold).
- [Concurrent fold passes across instances double-count] → single-transaction fold with row locks on rollup rows + leader election; watermark re-read inside the transaction makes passes idempotent (a second pass sees the advanced watermark and folds nothing).
- [Backfill load on huge tables] → sliced fold windows, background pool, leader-only; each slice is index-friendly (`requested_at` range).
- [Clock skew between instances corrupts the boundary] → boundary computed from DB-observed `now` minus lag inside the fold transaction; only the leader computes it.

## Migration Plan

1. Alembic migration on current head: create `account_usage_rollups` (`account_id` TEXT PK → accounts, BIGINT sum columns NOT NULL DEFAULT 0, `total_cost_usd` FLOAT NOT NULL DEFAULT 0) and `account_usage_rollup_state` (id INT PK, `folded_through` naive-UTC DATETIME NOT NULL — the repo stores naive UTC everywhere, not TIMESTAMPTZ), seeding the state row `(1, epoch)`. Downgrade drops both tables. No data copy at migration time.
2. Deploy; first fold pass backfills in the background. Reads are correct throughout (D3).
3. Rollback: revert code (old query path has no rollup dependency), downgrade drops the tables. No data loss — raw logs remain the source of record until the retention change lands.

## Open Questions

- None blocking. Whether fold cadence/lag should become operator settings is deferred until someone asks; constants keep the surface minimal.
