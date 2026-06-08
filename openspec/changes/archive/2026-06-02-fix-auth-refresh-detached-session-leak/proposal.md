## Why

`AuthManager.ensure_fresh` runs the OAuth token refresh as a **detached task** that the singleflight (`_RefreshSingleflight`) keeps alive with `asyncio.shield` — so concurrent waiters share one refresh and a cancelled waiter does not abort it. `AuthManager.refresh_account` then persists the rotated tokens through the manager's bound repo (`update_tokens`, and on permanent failure `get_by_id` / `update_status`).

When the manager is constructed on a **request-scoped** session, that detached task can outlive its session. The proxy path does exactly this: `ProxyService._ensure_fresh` opens `async with self._repo_factory() as repos` (→ `get_background_session()`) and builds `AuthManager(repos.accounts, ...)`.

On client disconnect mid-refresh, the request task is cancelled at `await asyncio.shield(task)`. The shield keeps the refresh task running, but the caller unwinds and the `async with` closes the request-scoped session in its `finally`. The still-running refresh task then calls `update_tokens` against that closed, concurrently-finalized `AsyncSession` (which is not safe for concurrent use). The connection it checks out is never returned to the pool.

Over time these stranded checkouts exhaust the background engine pool. Once exhausted, every request that needs a background session (e.g. the firewall allowlist lookup on `/v1/*` and `/backend-api/codex/*`) blocks for the full `database_pool_timeout_seconds` and then returns HTTP 500 (`sqlalchemy.exc.TimeoutError: QueuePool limit ... connection timed out`), and client retries pile more load on the dead pool.

Observed in a production multi-agent deployment: thousands of `QueuePool limit` 500s accumulating over hours, onset only after days of uptime (a slow leak of roughly one connection per refresh-interrupted-by-disconnect), strongly correlated with client reconnect storms, and not per-request — consistent with this detached-refresh path rather than the per-request firewall/auth reads.

## What Changes

- `AuthManager.__init__` gains an optional `refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]]`. A new `AuthManager._run_refresh` becomes the singleflight body: when a factory is provided it opens a **fresh** accounts repo (its own session) for the refresh write, so the detached task's session lifetime is independent of the caller's cancellation. When no factory is provided it falls back to the bound repo (callers whose session is not client-cancellable, e.g. the usage refresh scheduler).
- `ProxyService._ensure_fresh` passes `refresh_repo_factory=self._accounts_refresh_scope`, a new `@asynccontextmanager` that yields a fresh `repos.accounts` from the existing `self._repo_factory()`.
- Regression test in `tests/unit/test_auth_manager.py`: with a refresh in flight, the caller is cancelled (simulating a client disconnect) and the test asserts the refresh wrote through its own session (opened and closed) and never the request-scoped repo. Fails before the change, passes after.
- `ADDED Requirements` delta to the `database-backends` capability codifying that detached/shielded background tasks own their DB session lifetime.

## Impact

- Fixes the background-pool connection leak that, once the pool is exhausted, surfaces as `QueuePool limit ... connection timed out` 500s on all pool-backed paths until a restart.
- No env var, config, API, or migration change. Token-refresh behavior is unchanged on the happy path and for non-cancelled callers; the only change is that a refresh triggered from the proxy path uses its own DB session.
- Singleflight dedup, refresh admission control, recent-failure cooldown, and `_ensure_chatgpt_account_id` are unchanged. The usage refresh scheduler path is unchanged (it passes no factory).
