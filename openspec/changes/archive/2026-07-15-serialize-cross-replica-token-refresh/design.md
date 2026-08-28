# Design: serialize-cross-replica-token-refresh

## Decisions

### 1. Refresh claim row, not advisory locks

New table `account_refresh_claims`: `account_id` (String PK, FK `accounts.id` ON DELETE CASCADE), `claimed_by` (String(128) NOT NULL — bridge instance id plus a per-process suffix), `claimed_at`, `claim_expires_at` (DateTime NOT NULL). Acquisition is a single dialect-specific statement modeled on `RingMembershipService.register`:

```sql
INSERT ... VALUES (:account_id, :me, :now, :exp)
ON CONFLICT (account_id) DO UPDATE
SET claimed_by = :me, claimed_at = :now, claim_expires_at = :exp
WHERE account_refresh_claims.claim_expires_at < :now
   OR account_refresh_claims.claimed_by = :me
RETURNING account_id
```

Claim won iff RETURNING yields a row. Release: `DELETE ... WHERE account_id = :id AND claimed_by = :me`.

- **PostgreSQL**: ON CONFLICT DO UPDATE serializes concurrent claimers on the row lock; exactly one statement's WHERE passes.
- **SQLite**: the identical statement (`sqlite_insert.on_conflict_do_update`) is atomic under SQLite's database-level single-writer lock, safe across processes sharing one file via `busy_timeout`, wrapped in the repo's existing `sqlite_writer_section` for in-process serialization plus a short `database is locked` retry — the same pattern already proven in `DatabaseRateLimiter.check_and_increment` and the ring upsert.

**Rejected**:
- (a) `pg_advisory_xact_lock` around the refresh — would pin a pooled connection and open transaction across an upstream OAuth HTTPS exchange (`token_refresh_timeout_seconds=8s`), and its SQLite analog (`BEGIN IMMEDIATE` across upstream I/O) would block every writer in the deployment; advisory locks also die silently with the connection.
- (b) CAS-only on `update_tokens` — prevents clobbering but not the double upstream POST, and the double POST is the harm (reuse detection can revoke the whole token family).
- (c) Reusing `scheduler_leader` — global not per-account, and must work with leader election disabled (the default).

### 2. Claim lifecycle around the existing singleflight

The process-local `_REFRESH_SINGLEFLIGHT` stays (it dedupes intra-process waiters cheaply); the DB claim is acquired inside the singleflight body (`refresh_account`, which `_run_refresh` invokes in its owned-session scope), so at most one claim attempt per process per token generation.

- **Winner**: after acquiring, re-read the account with `get_by_id_fresh` (a real SELECT with `populate_existing`, defeating both READ COMMITTED staleness and the AsyncSession identity map that made the old guard unreliable); if the refresh-token fingerprint differs from the one the caller keyed on, release and adopt the latest row (someone already rotated) — zero upstream calls. Otherwise refresh using the re-read ciphertext (not the caller's possibly stale copy), persist via `update_tokens` with the new optional `expected_refresh_token_encrypted` CAS (WHERE compares the exact ciphertext bytes read under the claim), release the claim.
- **Loser**: poll every `token_refresh_claim_poll_seconds` re-reading claim + account; if the fingerprint changed → adopt the winner's tokens, return, zero upstream calls; if the claim expired/was released → the next loop iteration re-acquires; at the deadline (`token_refresh_claim_wait_seconds`, additionally capped by the caller's existing `asyncio.wait_for` budget in `_ensure_fresh`) → raise `RefreshError(code="refresh_claim_timeout", is_permanent=False, transport_error=True)`. `transport_error=True` keeps it out of the singleflight's failure cache and the permanent path, so the 401-recovery hot path fails over to another account rather than deadlocking or marking REAUTH.
- **No deadlock**: no DB lock is held across I/O on either backend (claim transactions are single-statement autocommit); liveness after claimant crash comes from `claim_expires_at` (TTL default 30s, startup-validated >= 2x `token_refresh_timeout_seconds`); the loser's wait is doubly bounded (own cap + request budget).

### 3. Permanent-failure guard hardening (works even for unclaimed callers)

Before persisting a permanent failure, re-read token material via `get_by_id_fresh` (real SELECT with `populate_existing`) rather than `session.get`, and write the status with the existing `update_status_if_current` CAS'd on the state observed in that fresh read (`status`, `deactivation_reason`, `reset_at`) — a concurrent re-auth/import/recovery can no longer be clobbered, and the sticky/bridge teardown only fires when the CAS applies.

### 4. Guardian dynamic multi-replica detection

Keep `enabled = auth_guardian_enabled and (leader_election_enabled or not static_multi_replica)` at build time, and add a per-tick guard in `_refresh_once`: when leader election is disabled, COUNT `bridge_ring_members` with `last_heartbeat_at` within `RING_STALE_THRESHOLD_SECONDS` (30s); if >1, skip the pass and log a warning naming `CODEX_LB_LEADER_ELECTION_ENABLED`. One COUNT per guardian tick (default hours apart) — negligible. This is defense-in-depth against duplicate upstream load; the claim is the hard correctness guarantee (deployments that never register ring members still cannot double-refresh).

**Rejected**: requiring leader election unconditionally on non-SQLite — breaks existing single-replica Postgres deployments that legitimately run with it disabled.

### 5. Migration

Revision `20260713_040000_add_account_refresh_claims`. **Re-parented after rebase onto main**: the usage-rollup migrations (`20260712_010000_add_account_usage_rollups`, `20260712_020000_add_api_key_usage_rollups`) have since merged to main, so this revision is now parented on the current main head `20260712_020000_add_api_key_usage_rollups` and renamed to a unique timestamp after it (an earlier iteration duplicated the `20260712_020000` id, which the rename resolves). Single head; downgrade drops the table; no backfill (empty coordination table).

### 6. Performance

Zero new per-request DB round-trips on the hot proxy path — claim machinery executes only when a refresh actually fires (8-day cadence or 401 recovery), adding ~3 short DB statements (claim upsert, `populate_existing` re-read, claim delete) around an upstream HTTPS exchange that already takes hundreds of ms.

## Deviations from the approved design

1. **Migration parent** — re-parented onto the current main head `20260712_020000_add_api_key_usage_rollups` (see Decision 5) after the usage-rollup migrations merged to main.
2. **Coordinator wiring** — the design left wiring open ("claim repository helpers"); implemented as a standalone `RefreshClaimCoordinator` in `app/modules/accounts/refresh_claims.py` that opens its own short-lived background sessions (single-statement autocommit, honoring the "no lock across upstream I/O" invariant) plus a process-default accessor (`get_refresh_claim_coordinator`). `AuthManager` uses the process default unless a coordinator is injected, so every product refresh path (per-request 401 recovery, guardian, usage refresh, warm-up, automations, model refresh) is claimed without per-call-site wiring. The test harness disables the process default (`set_refresh_claim_coordinator(None)`) so DB-less unit tests keep exercising the legacy flow; claim tests inject real coordinators explicitly.
3. **Adoption returns the caller's object** — when a loser/winner adopts a concurrently committed rotation, the fresh row's state is copied onto the caller's `Account` object (`_adopt_account_row`) instead of returning the repo-session-bound row, which would expire once the owned refresh session closes. The pre-existing stale-guard path had this latent detached-object hazard; the old unit test asserting object identity was updated to assert adopted state.
4. **Loser re-acquire** — the design called for "one bounded re-acquire attempt"; implemented as the poll loop naturally re-attempting acquisition each iteration (still bounded by the same deadline), which is strictly simpler and no less bounded.
5. **`update_tokens` value computation** — persisting now happens via locally computed values with the CAS, and the in-memory account object is mutated only afterwards. This avoids the ORM autoflush writing dirty attributes ahead of (and bypassing) the CAS UPDATE when the account object is attached to the same session. Behavior-preserving for all existing paths (covered by the existing workspace/plan unit tests).
