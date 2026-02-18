## Why

Authentication, error handling, and session management are implemented via ASGI middleware with manual path-matching, `request.state` side-channels, and scattered `SessionLocal()` calls. This duplicates route definitions, bypasses FastAPI's type-safe dependency injection, and forces workarounds for OpenAPI schema generation. Migrating to idiomatic FastAPI patterns (router-level `Depends`/`Security`, custom exceptions, consistent session factories) will eliminate these issues while preserving identical external behavior.

## What Changes

- **Auth middleware → router dependencies**: Replace path-based auth dispatch in `dashboard_auth.py` with `Security`/`Depends` guards declared on each router. Remove the auth middleware entirely.
- **`request.state` → typed parameters**: Eliminate `request.state.api_key` and `request.state.api_key_reservation`; inject validated `ApiKeyData` directly into handlers via dependencies.
- **Manual JSONResponse errors → custom exceptions**: Introduce domain exception classes (`ProxyAuthError`, `DashboardAuthError`, etc.) and register FastAPI exception handlers. Remove path-based branching in `exceptions.py`.
- **Inconsistent `SessionLocal()` → shared context managers**: Create `get_background_session()` for middleware/schedulers and standardize request-scoped session usage through existing `get_session` dependency.
- **Mixed response patterns → `response_model` + exceptions**: Use `response_model` for happy paths and raise exceptions for error cases instead of returning `JSONResponse` directly from handlers.
- **Settings access consolidation**: Keep `get_settings()` for static env config and `get_settings_cache()` for DB-backed dashboard settings; remove any redundant access patterns.
- **Session factory unification**: Replace three different session lifecycle patterns (manual try/finally, `@asynccontextmanager`, direct `async with`) with two: `Depends(get_session)` for requests, `get_background_session()` for non-request code.

## Capabilities

### New Capabilities

_(none — this is an internal refactor with no new user-facing capabilities)_

### Modified Capabilities

- `admin-auth`: Update spec language from middleware-specific to transport-agnostic auth validation. Session/TOTP/password verification behavior unchanged; implementation moves from middleware to dependency injection.
- `api-keys`: Update spec language for Bearer token validation. Move from middleware-based validation to router-level `Security` dependency. Reservation settlement flow unchanged.

## Impact

- **Code**: `app/core/middleware/dashboard_auth.py` (removed or gutted), `app/dependencies.py` (new auth providers), `app/core/handlers/exceptions.py` (new exception classes + handlers), `app/core/middleware/api_errors.py` (simplified or removed), all `app/modules/*/api.py` routers (add `dependencies=[...]`), `app/main.py` (remove `add_auth_middleware`), scheduler files (use shared session factory).
- **APIs**: No external contract changes. Same endpoints, same request/response formats, same error shapes.
- **Tests**: Auth-related test fixtures may need updating (mock dependencies instead of middleware). Error assertion targets may shift from status codes to exception types in unit tests.
