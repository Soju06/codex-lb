## Why

OAuth token exchange (initial login and device flow) bypasses per-account proxy routing because `_oauth_route()` calls `resolve_upstream_route` with `account_id=None`. When operators set per-account proxies after login, the initial token was issued from a different IP than all subsequent API calls and token refreshes. OpenAI invalidates these sessions, causing "Re-auth required" failures after hours/days. This is the root cause of issue #1057.

## What Changes

- When any active `AccountProxyBinding` records exist, `_oauth_route()` forces `strict=True` on `resolve_upstream_route()`, causing fail-closed behavior when no default pool is configured and routing through the default pool when one exists.
- Add fail-closed guard in `auth_manager._refresh_tokens`: when an account has a proxy binding but route resolution returns `None`, raise instead of silently using direct egress.

## Capabilities

### New Capabilities

### Modified Capabilities
- `outbound-http-clients`: OAuth bootstrap route resolution must fail closed when active proxy bindings exist and no default pool is resolvable. Token refresh must fail closed when an account binding exists but route resolution returns None.
