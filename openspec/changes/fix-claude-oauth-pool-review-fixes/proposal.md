## Why

The `add-claude-oauth-pool` change landed a working Claude OAuth pool, but a review of PR #1 surfaced correctness gaps that the original change did not address:

1. **Per-account refresh serialization is process-local only.** The singleflight in `ClaudeAuthManager` is a module-level `asyncio.Lock` + dict; in a multi-replica deployment, two replicas can both attempt to refresh the same Claude account concurrently. Because Anthropic's refresh tokens are single-use, a second concurrent refresh would invalidate the just-rotated refresh token and surface as `invalid_grant` on the next call — the account would be deactivated even though nothing was actually wrong with it.
2. **A "refresh token omitted" response is silently coerced.** When `ClaudeOAuthClient.refresh` returns a 200 body without a `refresh_token` field, the existing code drops the column to NULL but leaves the account ACTIVE. The next refresh will then fail with `invalid_grant` because there is no refresh token at all. The current spec says "flagged for re-authorization" but the code does not flag — it just deactivates on the next failed refresh.
3. **The streaming proxy leaks the upstream aiohttp response on unexpected exceptions.** The `_gen()` SSE wrapper in `app/modules/claude/api.py` only catches `ClaudeAuthError` and `ClaudeRateLimited`. Any other exception (transport disconnect, JSON parse error, `asyncio.CancelledError`) bypasses the cleanup path and the upstream aiohttp connection lingers in the pool until the iterator is garbage-collected.
4. **The new migrations are not exercised in a round-trip.** `make migration-check` and `make migration-check-postgres` are smoke tests for the forward direction only. The downgrade order in `20260701_000000_add_claude_account_columns.py` drops `accounts.email NOT NULL` and `accounts.provider` constraints in the same `batch_alter_table` block — the ordering is correct today, but no test asserts the `upgrade → downgrade → upgrade` cycle succeeds on either engine.

## What Changes

- Extend the Claude refresh path to acquire a per-account cross-process advisory lock before issuing `POST /v1/oauth/token`, using the project's `pg_advisory_xact_lock(hashtext(:key))` idiom.
- Treat a `None` refresh token in the OAuth response as a hard failure: log a structured `claude.refresh.refresh_token_missing` event and deactivate the account with reason `refresh_token_missing:<message>`.
- Tighten the streaming SSE wrapper to release the upstream iterator in a `finally` block for every exception class, while still emitting the typed-error envelope for known error classes.
- Add a single `upgrade → downgrade → upgrade` round-trip test for the two new migrations in `tests/unit/test_db_migrate.py`, asserting schema parity after the round-trip.
- Apply the nine nice-to-have cleanups (force parameter removal, admin-context doc tightening, lazy-session startup assertion, no-op inlining, type-hint hardening, gauge-semantics comment, JSON env-var parsing, i18n parity script) to leave the codebase in a state where no review comment lingers.

## Capabilities

### Modified Capabilities

- `claude-oauth-pool`: Strengthen the per-account refresh serialization requirement to span processes/replicas; add explicit handling for the "refresh token omitted" response; add streaming iterator cleanup on unexpected exceptions.
- `database-migrations`: Add a round-trip / reversibility requirement so the new migrations are exercised `upgrade → downgrade → upgrade` in CI.

## Impact

- **Database**: No schema changes. The CHECK predicate for `ck_accounts_claude_rt_required` is tightened to explicitly enumerate the allowed `(provider, claude_refresh_token_encrypted)` pairs (no behavioral change for valid data; cleaner failure mode for NULL-provider rows).
- **Backend Python**: `ClaudeAuthManager` gains a session-aware lock acquisition helper (Postgres path) and a deactivation path for the missing-refresh-token case. `app/modules/claude/api.py::_gen()` is restructured to use a `try / except / finally` cleanup. The auth guardian wiring in `app/core/auth/guardian.py` is updated to pass a session. `app/core/config/settings.py` gains JSON env-var parsing for `CODEX_LB_CLAUDE_OAUTH_EXTRA_HEADERS`.
- **Frontend**: No changes. The i18n parity script is a developer-only check.
- **Existing API surface**: No changes. All edits are internal correctness improvements.
- **No breaking changes**: Existing callers continue to work. The new lock acquisition adds at most one extra round-trip per refresh on Postgres (already inside the same transaction); SQLite is unchanged.
- **Verification**: `make migration-check`, `make migration-check-postgres`, `make test-unit`, `make test-integration-core`, `make architecture-check` must all pass; the new round-trip test must pass; the advisory-lock test must pass; the streaming-cleanup test must pass.
