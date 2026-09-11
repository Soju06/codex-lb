## 1. Schema

- [x] 1.1 Alembic revision `20260911_060000_add_bridge_session_continuity_abandonment` adding
  `continuity_abandoned_at` (timestamptz, nullable) and `continuity_abandonment_scope`
  (`String(32)`, nullable) to `http_bridge_sessions`, guarded by a column inspect and applied
  through `batch_alter_table` for SQLite, with a mirrored downgrade. Single head.
- [x] 1.2 Same-PR `app/db/models.py` update so `codex-lb-db check` reports no drift.
- [x] 1.3 `tests/integration/test_migrations.py::test_bridge_continuity_abandonment_migration_upgrade_and_downgrade`,
  reading the parent from the graph rather than a literal, plus a `POSTGRES_PYTEST_TARGETS` entry.

## 2. Repository

- [x] 2.1 `_bridge_continuity_is_abandoned` plus `continuity_abandoned` / `abandoned_account_id`
  on `DurableBridgeSessionSnapshot`, populated by both `_to_snapshot` and
  `_returned_row_to_snapshot` (add the two columns to `_SNAPSHOT_COLUMNS` so the fenced
  `RETURNING` path sees them).
- [x] 2.2 `retire_stale_unavailable_bridge_owners(cutoff, *, now)`: phase 1 tombstones rows whose
  owner is in `HARD_OWNER_UNAVAILABLE_STATUSES` past its reset horizon and whose `last_seen_at`
  predates the cutoff; phase 2 deletes tombstones older than the cutoff that own no operation
  rows, aliases first. Phase 1 deliberately omits the `~exists(operation)` guard.
- [x] 2.3 `claim_session` clears both markers on every successful claim, making retirement
  reversible.
- [x] 2.4 `HARD_OWNER_UNAVAILABLE_STATUSES` in `app/modules/proxy/account_eligibility.py`, beside
  `ROUTABLE_STATUSES`, documented as SQL-usable and grace-paired.

## 3. Read path

- [x] 3.1 `DurableBridgeLookup` gains `continuity_abandoned` / `retired_account_id`; `_to_lookup`
  strips owner, response anchor, turn state, input-prefix metadata and pending tool calls from a
  retired row. Applied in the one funnel so every lookup path is covered.
- [x] 3.2 `streaming.py`: `durable_owner_missing` and `model_transition_owner_missing` exempt
  retired lookups, so retirement frees the thread instead of swapping one 502 for another.

## 4. Scheduling

- [x] 4.1 Call the sweep from `StickySessionCleanupScheduler._cleanup_as_leader` beside the
  sticky hard-owner sweep, reusing `_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS`. No new
  setting. Log only when it retires something.

## 5. Regression coverage

- [x] 5.1 `tests/unit/test_durable_bridge_owner_retirement.py` (new): retired past the window;
  not retired inside it; never for a healthy owner; rate-limited with a future vs. past reset;
  `reauth_required`; operation rows survive phase 1; phase 2 deletes without a ledger and keeps
  with one; idempotent; a fresh claim clears it; ownerless rows left to the existing purges; the
  status set is closed.
- [x] 5.2 `tests/integration/test_repositories.py`: a `reauth_required` owner's row survives the
  status transition, is not retired inside the window, is retired past it, keeps its aliases and
  ledger, and then reads back ownerless with `retired_account_id` set. Added to
  `POSTGRES_PYTEST_TARGETS` so the SQL runs on PostgreSQL.
- [x] 5.3 `tests/integration/test_http_responses_bridge.py`: end-to-end — a bridged thread whose
  owner is paused and swept returns **200** on a healthy account and never re-selects the retired
  owner. Waits for the durable row rather than assuming it landed before the response returned.
  Runs over the Codex backend surface with a thread identity so the bridge key is a hard
  `thread_header`: a soft prompt-cache key falls back to a healthy account on its own and would
  pass without the fix. Verified by removing the `continuity_abandoned` exemption and watching it
  fail.
- [x] 5.4 `tests/unit/test_sticky_session_cleanup_scheduler.py`: the sweep runs on the same pass
  with the same grace window; stub it in every scheduler test.

- [x] 5.5 ~~Scope the post-downgrade drift assertion in
  `tests/integration/test_affinity_invite_migration.py`.~~ Dropped on rebase: `main` fixed the
  same defect first, and more cleanly — it removes the post-downgrade drift assertion outright
  with the same diagnosis ("true only by accident, and false for the first descendant that adds a
  column"). `main`'s version is kept.

## 6. Validation

- [x] 6.1 `make lint`, `make typecheck`.
- [x] 6.2 `make migration-check` (`migration_policy=ok`, `schema_drift=none`, single head).
- [x] 6.3 `make test-unit` (10,049), `make test-integration-bridge` (342),
  `make test-integration-core-1` (869), `-2` (910), `-3` (1,036), `tests/e2e` (27).
- [x] 6.4 `openspec validate retire-unroutable-bridge-continuity-owners --strict`,
  `openspec validate --specs` (65 passed).
- [x] 6.5 `codex review --base origin/main` — one finding fixed (a retired lookup still named
  its owning replica), two rebutted with reasons recorded in `context.md`.
