## 1. Failing test (proves the bug exists)

- [x] 1.1 Write unit test: OAuth `_oauth_route()` with active bindings and no default pool MUST raise instead of returning None (direct egress)
- [x] 1.2 Write unit test: OAuth `_oauth_route()` with active bindings and a default pool MUST resolve a route from that pool
- [x] 1.3 Write unit test: `auth_manager._refresh_tokens` with an account that has a proxy binding but route returns None MUST raise RefreshError instead of allowing direct egress

## 2. Fix OAuth bootstrap route resolution

- [x] 2.1 Modify `_oauth_route()` to pass `strict=True` when any active `AccountProxyBinding` records exist, forcing fail-closed behavior when no default pool is configured
- [x] 2.2 Verify all OAuth call sites (`manual_callback`, `_handle_callback`, `_start_device_flow`, `_poll_device_tokens`) use the updated `_oauth_route()`

## 3. Fix token refresh fail-closed guard

- [x] 3.1 In `auth_manager._refresh_tokens`, when `route is None` and the account has an active proxy binding, raise `RefreshError` with `upstream_proxy_fail_closed_reason`

## 4. Validation

- [x] 4.1 Run `openspec validate --specs` — must pass
- [x] 4.2 Run `uv run pytest tests/unit/test_oauth_proxy_route.py tests/unit/test_auth_manager.py tests/unit/test_auth_refresh.py tests/unit/test_oauth_client.py tests/unit/test_upstream_proxy_resolver.py -q` — all tests green
- [x] 4.3 Run `uv run ruff check app/modules/oauth/service.py app/modules/accounts/auth_manager.py` — clean
