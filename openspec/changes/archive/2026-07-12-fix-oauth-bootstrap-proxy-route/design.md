## Context

`_oauth_route()` in `app/modules/oauth/service.py:64-74` calls `resolve_upstream_route` with `account_id=None`. Since the account row is only created inside `_persist_tokens` *after* the token exchange, there is no `account_id` to bind a proxy against during OAuth. All post-login paths correctly pass `account_id=account.id`.

When operators configure per-account proxies after login, the initial token was issued from a different IP (direct egress or default pool) than all subsequent API calls. OpenAI invalidates these sessions, causing "Re-auth required" after hours/days.

## Goals / Non-Goals

**Goals:**
- OAuth token exchange uses a proxy pool when strict routing is enabled
- Fail closed instead of silent direct egress when no pool is resolvable
- Prevent IP split in token refresh when account binding becomes unavailable

**Non-Goals:**
- No schema changes (no `pending_oauth_pool_id` column) — use the existing default pool mechanism
- No UI changes for pre-binding a proxy before OAuth start
- No changes to post-login routing (already correct)

## Decisions

### 1. Use default pool for OAuth bootstrap, not a new pool-hint parameter

**Decision:** `_oauth_route()` queries whether any active `AccountProxyBinding` records exist. If they do, it passes `strict=True` to `resolve_upstream_route()`, which overrides the dashboard's `upstream_proxy_routing_enabled` flag and forces the resolver to require a default pool (or fail closed). When no bindings exist, `strict=None` preserves the previous behavior (defer to dashboard setting or direct egress).

**Why binding-existence, not `upstream_proxy_routing_enabled`:** Per-account proxy bindings work even when the dashboard flag is `False` — the resolver finds the binding before checking the flag. An operator who has bindings but hasn't toggled the dashboard flag would still have OAuth go direct, creating the IP split. Checking binding existence is the more direct signal that the operator cares about proxy routing.

**Why not pool hint:** Requires schema change to `OAuthState`, UI changes, and API parameter plumbing. The default pool already exists for exactly this purpose — bootstrap traffic that has no account binding yet.

### 2. Fail closed in token refresh when binding expected but route is None

**Decision:** In `auth_manager._refresh_tokens`, after `resolve_upstream_route` returns `None`, check if the account has a proxy binding. If it does, raise `RefreshError` instead of allowing direct egress.

**Why:** A binding that becomes inactive between checks (race condition, operator toggle) would silently cause the refresh to go direct, re-introducing the IP split. Failing closed is safer — the refresh fails, the account enters error backoff, and the operator is alerted.

## Risks / Trade-offs

- **[OAuth fails when default pool not configured]** → This is intentional. Operators who enable strict routing MUST configure a default pool. The error message guides them.
- **[Existing accounts with direct-egress OAuth still have the IP split]** → Out of scope. Operators should re-authenticate after configuring the default pool. A follow-up could add a "force re-auth" button.
- **[Token refresh fail-closed may increase transient failures]** → The singleflight cache + error backoff already handle transient failures. The permanent failure path only triggers after retries.
