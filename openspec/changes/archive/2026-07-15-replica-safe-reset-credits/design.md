# Design — replica-safe-reset-credits

## Decisions

### 1. Idempotency ledger = DB table, not bus or memory

Table `reset_credit_redeem_requests`: `account_id` (String, FK `accounts.id`
ON DELETE CASCADE), `redeem_request_id` (String), composite PK
`(account_id, redeem_request_id)`, `credit_id` (String NOT NULL), `created_at`
(DateTime tz-aware NOT NULL, indexed). Written with
`INSERT ... ON CONFLICT DO NOTHING` (first-writer-wins, belt-and-braces since
writes are already serialized per account) on a short-lived dedicated session
committed BEFORE the upstream consume call, so a consume whose HTTP response is
lost is still pinned for any replica's retry. Read at the top of the locked
section, replacing `store.get_redeem_request_credit_id` /
`remember_redeem_request`; the in-memory map is removed entirely to avoid a
divergent L1. TTL: rows older than 24h for the same account are deleted in the
same write transaction — no new scheduler; the table stays tiny because
redeems are rare human-driven ops. Rows are kept on consume failure so a
same-request retry retargets the same credit (never a second one); a new user
action generates a new `redeem_request_id`.

Rejected: putting the map in the cache_invalidation bus (it is a version
counter, carries no payload) and Redis/etc. (no such dependency in this repo).

### 2. SQLite serialization = atomic claim row; PostgreSQL unchanged

`serialize_reset_credit_redeem` keeps three arms:

- **postgresql**: existing
  `pg_advisory_xact_lock(hashtext('reset-credit-redeem:{account_id}'))` on the
  caller's session — blocking, transaction-scoped, zero cleanup (unchanged).
- **sqlite (session present)**: new table `reset_credit_redeem_claims`
  (`account_id` String PK FK accounts.id ON DELETE CASCADE, `holder_id`
  String(100) NOT NULL — a uuid4 nonce per acquisition, `expires_at` DateTime
  NOT NULL). Acquire via a single-statement
  `INSERT ... ON CONFLICT(account_id) DO UPDATE SET holder_id = excluded...,
  expires_at = excluded... WHERE reset_credit_redeem_claims.expires_at < :now`
  on a dedicated short session committed immediately — rowcount 1 means
  claimed, 0 means held by a live claimant; retry every 100ms up to 15s then
  raise `RedeemClaimTimeoutError`, which each surface maps to its own error
  envelope (dashboard: 409 `reset_credit_redeem_in_progress` conflict;
  `POST /v1/reset-credit`: `HTTPException(409)` rendered in the `/v1/*` OpenAI
  envelope); while the section runs the holder renews the lease every 10s via
  a heartbeat task (same renew pattern as the `scheduler_leader` lease) so a
  redemption slower than one lease — usage-fetch retries plus the upstream
  consume can exceed 30s — is not taken over mid-section; release in `finally`
  via `DELETE WHERE account_id = :a AND holder_id = :h`; crash recovery via the
  30s lease expiry once renewals stop (same conditional-upsert shape as the
  `scheduler_leader` lease, proven atomic under SQLite's single-writer lock).
  Claim statements deliberately use their own sessions, never the caller's
  transaction.
- **session=None / other dialects** (direct unit-test callers): existing
  in-process `asyncio.Lock` retained.

Rejected: `BEGIN IMMEDIATE` held across the upstream consume (would hold the
SQLite write lock across a multi-second network call, starving the whole app);
keeping a process-local lock in front of the DB claim on SQLite (rejected so
the two-task integration test exercises the real product path — the DB claim
is the sole serializer when a DB session is present).

### 3. Snapshot invalidation = new bus namespace, full-store clear

`NAMESPACE_RESET_CREDITS = "reset_credits"` added next to
`NAMESPACE_API_KEY`/`NAMESPACE_FIREWALL`; `main.py` registers
`cache_poller.on_invalidation(NAMESPACE_RESET_CREDITS, store.invalidate)`
(whole-store clear for PEERS). Bumped best-effort after
`store.invalidate(account_id)` in the dashboard consume path and at both
invalidate sites in `v1_redeem_reset_credit`, via
`bump_cache_invalidation_local` / `CacheInvalidationPoller.bump_local`. The bus
carries no payload, so peers do a FULL store clear; accepted because redeems
are rare and the <=60s refresh tick repopulates. The v1 upstream-fetch
fallback (decision 4) makes the false-409 fix independent of bus delivery; the
bus only shortens listing staleness from <=60s to ~0.5s.

`bump_local` bumps the shared counter and then records the resulting version as
already-observed on the ORIGINATING poller, so the source replica does NOT
re-run the whole-store clear for its own bump — it already evicted the affected
account precisely, and re-clearing would discard still-valid snapshots for
unrelated accounts and force redundant upstream refetches. `_known_versions` is
only advanced (`max`), never rewound, so a concurrent poll cannot be forced to
re-fire. A peer bump that coalesces into the acknowledged version on the source
degrades to the per-replica refresh fallback (identical to a lost bump); peers
are never affected. Rejected: a payload-carrying bus or per-account namespaces
(same reasons below) — self-suppression keeps the payload-free counter while
removing the source-side amplification.

Rejected: per-account dynamic namespaces (would leak unbounded rows into
`cache_invalidation`, scanned every 0.5s by every replica); moving snapshots
into a shared DB table (the full fix for staleness + amplification + empty
store at once, but a much larger schema/read-path change — flagged as a
follow-up).

### 4. v1 POST authoritative fallback

When `_select_available_reset_credit_by_id` misses, fetch upstream inside the
serialized section using the already-refreshed credentials and the resolved
route; if the `redeem_id` is available there, proceed and `store.set` the
fresh snapshot; else 409 AND cache the fresh snapshot (mirrors the dashboard
"fresh empty fetch replaces stale snapshot" rule). Adds one upstream
round-trip only on snapshot miss — never on the hot proxy path. Consequence:
route resolution and credential refresh now run before the 409 on a snapshot
miss (they are prerequisites for the authoritative fetch).

### 5. Scheduler: per-replica refresh retained, ticks desynchronized

Leader-gating rejected — the snapshot store is process-local, so non-leader
replicas would serve `available_reset_credits: 0` forever, violating the
existing "Every replica refreshes its local cache" requirement. Instead each
replica applies a uniform(0, interval) startup delay and a
uniform(0.9, 1.1) per-tick multiplier so replicas desynchronize. Aggregate
upstream fetch rate still scales with replica count;
`rate_limit_reset_credits_refresh_interval_seconds` is the operator control
(documented normatively). Rejected: dividing the interval by bridge ring size
(ring membership may legitimately be empty in non-bridge deployments; couples
an unrelated subsystem).

### 6. Migration

Single revision `20260713_070000_add_reset_credit_redeem_tables` creating both
tables plus the `created_at` index; downgrade drops them; no backfill needed
(both tables start empty by design).

## Deviations from the original design

- `down_revision` is `20260712_020000_add_api_key_usage_rollups` (the current
  main head after rebasing onto the merged usage-rollup revisions), following
  the rebase path the design itself prescribed.
- The scheduler's jitter test seam is an injectable `random.Random` instance
  (`rng` field), matching the design's "injectable rng" option.

## Performance

Zero new work on the hot proxy path; per-redeem cost is +1 ledger read, +1
ledger write/commit, and on SQLite +1 claim upsert and +1 delete — redeems are
rare dashboard/self-service actions. The bus bump adds one version-counter
upsert per redeem; the 0.5s poll already exists.
