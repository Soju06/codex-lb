# Tasks — replica-safe-reset-credits

1. [x] Create `openspec/changes/replica-safe-reset-credits/` with proposal,
   design, tasks, context, and delta specs for `rate-limit-reset-credits` and
   `api-keys`; run strict openspec validation.
2. [x] Add `ResetCreditRedeemRequest` and `ResetCreditRedeemClaim` models to
   `app/db/models.py` (FK `accounts.id` ON DELETE CASCADE).
3. [x] Add Alembic revision `20260713_070000_add_reset_credit_redeem_tables`
   on parent `20260712_020000_add_api_key_usage_rollups`; upgrade
   creates both tables + `created_at` index, downgrade drops them; extend
   `tests/integration/test_migrations.py` coverage.
4. [x] Add `app/modules/rate_limit_reset_credits/redeem_coordination.py`:
   `try_acquire_redeem_claim` / `acquire_redeem_claim` /
   `release_redeem_claim` (SQLite atomic conditional upsert, 30s lease, 100ms
   retry up to 15s, holder nonce) and ledger helpers
   `get_pinned_redeem_credit_id` / `pin_redeem_request` (INSERT ON CONFLICT DO
   NOTHING + opportunistic 24h TTL purge), all on dedicated short-lived
   `SessionLocal` sessions.
5. [x] Rework `serialize_reset_credit_redeem` in
   `app/modules/rate_limit_reset_credits/api.py`: postgresql -> existing
   advisory lock (unchanged); sqlite with DB session -> claim row (in-process
   lock removed from this arm); session=None/other -> existing in-process
   lock; map claim timeout to a 409 conflict.
6. [x] Replace `store._pending_redeems` usage in
   `_redeem_soonest_reset_credit_locked` with the DB ledger: read pinned
   credit at top of locked section, pin selected credit (commit) before
   `effective_consume_fn`; delete `remember_redeem_request` /
   `get_redeem_request_credit_id` from `store.py` and update unit tests.
7. [x] Add `NAMESPACE_RESET_CREDITS` to `app/core/cache/invalidation.py` (plus
   a `bump_cache_invalidation` helper); register the store-invalidate callback
   in `app/main.py`; bump best-effort after successful consume in the
   dashboard path and at both invalidate sites in `v1_redeem_reset_credit`.
8. [x] Add upstream-fetch fallback to `v1_redeem_reset_credit` in
   `app/modules/proxy/api.py`: on snapshot/credit miss, refresh credentials,
   `fetch_reset_credits` inside the serialized section, proceed if available
   (and cache the fresh snapshot) else 409 + cache fresh snapshot.
9. [x] Add startup jitter (uniform(0, interval)) and per-tick jitter
   (uniform(0.9, 1.1) * interval) to `RateLimitResetCreditsRefreshScheduler`
   with an injectable `rng` test seam.
10. [x] Write `tests/integration/test_reset_credits_replica_safety.py`
    (two-replica idempotency at the dashboard route, SQLite claim
    serialization at the route + primitives, v1 fresh-replica fallback,
    cross-replica bus invalidation, claim lease expiry takeover, ledger
    first-writer-wins + TTL purge).
11. [x] Add unit tests: sqlite claim arm + timeout mapping; scheduler jitter
    bounds; update existing api tests for the removed in-memory pending map.
12. [x] Run targeted `uv run pytest`, `uv run ruff`,
    `openspec validate replica-safe-reset-credits`; verify single Alembic
    head.
13. [x] (review follow-up) Renew the SQLite claim lease while the redeem
    section runs: `renew_redeem_claim` + `renew_redeem_claim_periodically`
    heartbeat (10s cadence vs 30s lease) spawned by the sqlite arm of
    `serialize_reset_credit_redeem`; cancel before release; integration
    coverage for takeover rejection past the original lease.
14. [x] (review follow-up) Surface claim-contention timeouts in each caller's
    native envelope: `serialize_reset_credit_redeem` raises
    `RedeemClaimTimeoutError`; the dashboard path maps it to the 409
    `reset_credit_redeem_in_progress` conflict and `POST /v1/reset-credit`
    maps it to an `HTTPException(409)` rendered in the `/v1/*` OpenAI
    envelope; regression tests at both surfaces.
15. [x] (review follow-up) Close scarce-credit accounting gaps on the redeem
    idempotency ledger and v1 path: (a) dashboard consume returns
    `no_available_reset_credit` (409) when a `redeem_request_id` has no durable
    pin and the fresh fetch shows nothing available, instead of pinning and
    consuming a stale cached credit; (b) `pin_redeem_request` purges expired
    rows BEFORE inserting so a `redeem_request_id` reused after its prior row
    aged past the 24h TTL re-pins to the new credit; (c) `POST /v1/reset-credit`
    always re-validates the requested credit against a fresh upstream fetch
    after winning the cross-replica claim rather than trusting the replica-local
    cache. Regression tests at the dashboard route, v1 route, and ledger
    primitive.
