# Replica-Safe Reset-Credit Redemption

## Why

Reset-credit redemption is only safe within one process today. The
`redeem_request_id -> credit_id` idempotency map lives in process memory, so a
retry routed to another replica consumes a SECOND paid credit. On SQLite the
per-account redeem serialization falls back to a process-local `asyncio.Lock`,
letting two processes sharing one DB file double-redeem. `POST /v1/reset-credit`
validates the requested `redeem_id` against a replica-local snapshot only,
returning false 409s from freshly started replicas. And post-redeem snapshot
invalidation never reaches peers, which keep listing redeemed credits for up to
60s. All four defects burn scarce paid credits or break the operator/API
contract in any multi-replica or multi-process deployment.

## What Changes

- New shared-DB idempotency ledger table `reset_credit_redeem_requests`
  (PK `(account_id, redeem_request_id)` -> `credit_id`, 24h TTL rows purged
  opportunistically at write time), written and committed inside the
  per-account serialized section BEFORE the upstream consume call; replaces the
  process-local pending-redeem map so a retry on any replica resolves to the
  originally chosen credit.
- New `reset_credit_redeem_claims` table (PK `account_id`, `holder_id`,
  `expires_at`) giving SQLite real cross-process per-account redeem
  serialization via a single atomic conditional upsert claim (30s lease,
  bounded retry, release-on-exit); PostgreSQL keeps the existing
  `pg_advisory_xact_lock` path unchanged.
- `POST /v1/reset-credit`: when the local snapshot misses the requested
  `redeem_id`, fall back to a live upstream `fetch_reset_credits` inside the
  serialized section and treat it as authoritative before returning 409;
  repopulate the store from the fresh fetch.
- New `reset_credits` namespace on the existing cache-invalidation
  version-counter bus, bumped after every successful consume (dashboard + v1)
  and on consume-conflict invalidation; every replica's poller clears its
  reset-credits store within ~0.5s instead of waiting up to 60s.
- Refresh scheduler keeps per-replica refresh but gains a randomized startup
  delay (uniform 0..interval) and per-tick jitter (+/-10%) so replicas do not
  tick in lockstep; the N-fold upstream amplification is documented normatively
  with the interval knob as the operator control.
- One Alembic migration adding both tables.
- Spec deltas in `rate-limit-reset-credits` and `api-keys`; regression tests
  simulating two replicas at the failing product paths.
