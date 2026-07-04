# Notes

## Lock-scope naming convention

The project standardizes on `<scope>:<key>` strings hashed via Postgres `hashtext`:

- `account-id:{id}` — `app/modules/accounts/repository.py::_acquire_postgresql_identity_lock`
- `merge-email:{email}` — `app/modules/accounts/repository.py::_acquire_postgresql_merge_lock`
- `reset-credit-redeem:{account_id}` — `app/modules/rate_limit_reset_credits/api.py::_acquire_postgresql_reset_credit_redeem_lock`
- `rate-limiter:{type}:{key}` — `app/core/rate_limiter/db_rate_limiter.py`

Claude refresh reuses the same idiom: `claude-refresh:{account_id}`. The `claude-` prefix is disjoint from all existing scopes and clearly labels the namespace as Claude-OAuth-pool-specific.

## Why a Postgres advisory lock (not `SELECT ... FOR UPDATE`)

`SELECT ... FOR UPDATE` on the `accounts` row would serialize more than necessary: every other code path that touches an `accounts` row (selection cache, routing, rate-limiter, dashboard) would queue behind the refresh. Advisory locks are purpose-built for "serialize a side-effect on a resource without locking the resource itself."

## Why not leader election for the 401-retry path

`LeaderElection.try_acquire()` returns False on a non-leader replica, which means a non-leader replica would have to **skip** the request-time 401 rotation entirely. The 401-retry path is request-driven and cannot wait for a leader tick (the request would time out before a leader could be elected). Per-account advisory locks let every replica attempt the rotation, and the lock guarantees only one wins.

## Refresh-token-omitted: what to do

Per Anthropic's documented contract, the refresh endpoint always returns a new `refresh_token`. The verified captures in `add-claude-oauth-pool/notes.md` confirm this. The defensive branch handles a future server behavior change. Silently keeping the old refresh token would be unsafe because Anthropic's single-use rotation means the next refresh on the old token would 400 with `invalid_grant` — a confusing failure mode. Deactivating the account explicitly is the only safe response.

## Streaming iterator cleanup: why `try/except/finally` (not `BaseException`)

Catching `BaseException` would mask `asyncio.CancelledError` from the FastAPI handler, leaking the cancellation. The `finally` block is the right place to ensure cleanup while letting the exception propagate to the framework.
