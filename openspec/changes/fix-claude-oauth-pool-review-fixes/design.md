# Design notes

## Cross-replica refresh serialization

The Codex OAuth refresh path relies on **scheduler-level leader election**: the entire `_refresh_once` tick is gated on `leader_election.try_acquire()`, and `_run_claude_refresh_pass` currently runs inside that gated block. That covers the guardian-scheduler path, but the **request-time 401-retry path** in `ClaudeProxyService._send_with_retry` and `stream_messages` is request-driven and cannot wait for a leader tick. Leader election alone is therefore insufficient.

The project's established idiom for per-resource cross-process serialization of a side-effect is **`pg_advisory_xact_lock(hashtext(:key))`** with a deterministic scope prefix string. The closest existing template is `app/modules/rate_limit_reset_credits/api.py::_acquire_postgresql_reset_credit_redeem_lock`, which acquires a per-account lock for the duration of a credit redeem side-effect and commits inside the lock window. Adopting the same shape for Claude refresh means:

1. Acquire `pg_advisory_xact_lock(hashtext("claude-refresh:{account_id}"))` at the top of `ClaudeAuthManager._run_refresh`.
2. The lock is automatically released at transaction commit (`_xact_lock` family), so we cannot leak it.
3. The existing in-process `_CLAUDE_REFRESH_SINGLEFLIGHT` continues to handle intra-process coalescing; the advisory lock extends that guarantee to cross-process.
4. For SQLite (single-process) deployments, the in-process singleflight alone is sufficient and the advisory lock is skipped with a `logger.debug` line.

Scope string convention: `"claude-refresh:{account_id}"` matches the established pattern (`"reset-credit-redeem:{account_id}"`, `"merge-email:{email}"`, `"account-id:{id}"`) and is disjoint from the other advisory-lock namespaces in use. `hashtext` is the project's chosen hash function for advisory-lock keys (see `app/db/alembic/versions/` migrations and `app/modules/rate_limit_reset_credits/api.py`).

## Session lifecycle

`ClaudeAuthManager._run_refresh` does not currently hold a session. Today the persist step uses the session that `ClaudeAccountRepository` was constructed with in the caller. To add the advisory lock, we need a fresh session in the auth manager itself. We add a `repo_factory: Callable[[], AsyncContextManager[AsyncSession]]` field on `ClaudeAuthManager` (mirroring `accounts/auth_manager.py::AuthManager.refresh_repo_factory`); the auth guardian's `_default_claude_auth_manager_factory` constructs the manager with a `get_background_session()` factory so the lock session and the persist session are the same transactional unit (the lock auto-releases on commit).

## Refresh-token-missing handling

`ClaudeRefreshResult.refresh_token` is `str | None`. The current code at `auth_manager.py:344-364` already documents the defensive branch: when the response omits the field, set the column to NULL. But the account is left ACTIVE, so the next request's 401 will fire an `invalid_grant` deactivation. The fix:

1. Detect `result.refresh_token is None` inside `_run_refresh` (before `_persist_rotated_credentials`).
2. Emit a structured `event=claude.refresh.refresh_token_missing`, `account_id=<id>`, `severity=warning` log line.
3. Deactivate the account with `deactivation_reason="refresh_token_missing:<message>"`.
4. Return `None` to the caller, matching the existing `invalid_grant` return contract (guardian logs the disabled event; proxy service treats `None` as "abort, don't retry").

## Streaming iterator cleanup

The current `_gen()` wrapper in `app/modules/claude/api.py` catches only `ClaudeAuthError` and `ClaudeRateLimited`. The fix is structural:

```python
async def _gen() -> Any:
    try:
        async for chunk in iterator:
            if chunk.kind == "sse":
                yield chunk.data
    except (ClaudeAuthError, ClaudeRateLimited) as exc:
        # ... emit typed-error SSE envelope
    finally:
        await _safe_aclose(iterator)
```

`BaseException` is intentionally not caught at the `except` level — we want the inner `try` to re-raise `asyncio.CancelledError` and other low-level exceptions so the FastAPI `StreamingResponse` machinery can surface them. The `finally` block guarantees iterator cleanup regardless of the exception type.

## Migration round-trip

Pattern from `tests/unit/test_db_rate_limiter.py::test_migration_upgrade_downgrade_upgrade_is_reversible`:

```python
command.upgrade(cfg, "head")
# assert schema is at head
command.downgrade(cfg, "base")
# assert schema is at base
command.upgrade(cfg, "head")
# assert schema matches the post-first-upgrade snapshot (idempotent forward)
```

For the Claude migration we additionally:

1. Insert a sample `accounts` row before downgrade to confirm the column drops don't leave a half-restored state.
2. Compare `inspect_migration_state(url).current_revision` to the head after the round-trip.
3. Run on both sqlite (default) and, if `CODEX_LB_TEST_DATABASE_URL` is a Postgres URL, on Postgres.

The test belongs in `tests/unit/test_db_migrate.py` (sync) rather than `tests/integration/test_migrations.py` (async) because the round-trip is a one-shot and the file already has two near-identical round-trip templates to mirror.
