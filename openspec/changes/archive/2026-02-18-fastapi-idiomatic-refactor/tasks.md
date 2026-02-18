## 1. Domain Exceptions & Handlers

- [x] 1.1 Create `app/core/exceptions.py` with base `AppError` and subclasses: `ProxyAuthError`, `ProxyModelNotAllowed`, `ProxyRateLimitError`, `DashboardAuthError`, `DashboardNotFoundError`, `DashboardConflictError`
- [x] 1.2 Register exception handlers in `app/core/handlers/exceptions.py` for each domain exception class — format responses using existing `openai_error()` / `dashboard_error()` helpers
- [x] 1.3 Add error format marker dependency (`set_openai_error_format`, `set_dashboard_error_format`) and update `RequestValidationError` / `StarletteHTTPException` handlers to use the marker instead of path-based branching

## 2. Session Lifecycle Standardization

- [x] 2.1 Add `get_background_session()` async context manager to `app/db/session.py`
- [x] 2.2 Migrate `dashboard_auth.py` inline `SessionLocal()` calls to `get_background_session()`
- [x] 2.3 Migrate `proxy/api.py` inline `SessionLocal()` calls (reservation enforcement/settlement) to `get_background_session()`
- [x] 2.4 Migrate `model_refresh_scheduler.py` and `usage/refresh_scheduler.py` to `get_background_session()`
- [x] 2.5 Migrate `settings_cache.py` to `get_background_session()`
- [x] 2.6 Remove `_safe_rollback` / `_safe_close` helpers from `dependencies.py` and scheduler files (replaced by context manager)

## 3. Auth Dependencies

- [x] 3.1 Create `app/core/auth/dependencies.py` with `validate_proxy_api_key()` — uses `Security(HTTPBearer(auto_error=False))`, returns `ApiKeyData | None`, raises `ProxyAuthError` on failure
- [x] 3.2 Create `validate_dashboard_session()` dependency — checks cached settings, validates session cookie, raises `DashboardAuthError` on failure
- [x] 3.3 Create `validate_codex_usage_identity()` dependency — validates Bearer token + `chatgpt-account-id` against accounts and upstream, raises `ProxyAuthError` on failure

## 4. Router Wiring

- [x] 4.1 Add `dependencies=[Security(validate_proxy_api_key)]` to `proxy_api.router` and `proxy_api.v1_router`
- [x] 4.2 Add `dependencies=[Depends(validate_codex_usage_identity)]` to `proxy_api.usage_router`
- [x] 4.3 Add `dependencies=[Depends(validate_dashboard_session)]` to dashboard module routers: `accounts_api`, `dashboard_api`, `usage_api`, `request_logs_api`, `settings_api`, `api_keys_api`
- [x] 4.4 Add error format marker dependencies: `set_openai_error_format` on proxy routers, `set_dashboard_error_format` on dashboard routers
- [x] 4.5 Remove `_bearer` global Security from `app/main.py` (replaced by per-router auth deps)

## 5. Handler Migration — Remove request.state & Manual JSONResponse

- [x] 5.1 Replace `_request_api_key(request)` in `proxy/api.py` with `api_key: ApiKeyData | None = Depends(validate_proxy_api_key)` parameter on handlers that need the value
- [x] 5.2 Replace `request.state.api_key_reservation` read/write in `proxy/api.py` with local variable or handler parameter flow
- [x] 5.3 Migrate error returns in `proxy/api.py` from `JSONResponse(content=openai_error(...))` to raising domain exceptions (`ProxyAuthError`, `ProxyModelNotAllowed`, `ProxyRateLimitError`)
- [x] 5.4 Migrate error returns in `accounts/api.py` from `JSONResponse(content=dashboard_error(...))` to raising domain exceptions
- [x] 5.5 Migrate error returns in `api_keys/api.py` from `JSONResponse(content=dashboard_error(...))` to raising domain exceptions
- [x] 5.6 Migrate error returns in `dashboard_auth/api.py` from `JSONResponse(content=dashboard_error(...))` to raising domain exceptions
- [x] 5.7 Migrate error returns in `settings/api.py`, `request_logs/api.py`, `dashboard/api.py` to domain exceptions

## 6. Middleware Removal

- [x] 6.1 Remove `add_auth_middleware()` call from `app/main.py`
- [x] 6.2 Delete or gut `app/core/middleware/dashboard_auth.py` (preserve only shared helpers like `_extract_bearer_token` if used by dependencies)
- [x] 6.3 Remove `add_api_unhandled_error_middleware()` call and delete `app/core/middleware/api_errors.py` (unhandled errors now caught by domain exception handlers)
- [x] 6.4 Update `app/core/middleware/__init__.py` exports

## 7. Test Updates

- [x] 7.1 Update auth-related integration tests to verify behavior through dependency injection (mock `validate_proxy_api_key`, `validate_dashboard_session` instead of middleware side-effects)
- [x] 7.2 Update error assertion tests — verify domain exceptions produce correct status codes and response shapes
- [x] 7.3 Verify all existing tests pass with no regressions (`pytest tests/ -x`)
