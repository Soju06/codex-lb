## Context

The app currently uses a single ASGI middleware (`dashboard_auth.py`) to handle all authentication. This middleware inspects `request.url.path` to decide which validation strategy to apply, then stuffs results into `request.state`. Exception handlers and error middleware also branch on paths. Session lifecycle management uses three different patterns across middleware, dependencies, and background schedulers.

FastAPI's dependency injection system (`Depends`, `Security`) is designed to solve exactly these problems — auth, context, and resource lifecycle — at the router level with type safety. The current middleware approach predates the codebase's adoption of FastAPI idioms in other areas (module contexts, service injection).

## Goals / Non-Goals

**Goals:**
- Eliminate the auth middleware; declare auth requirements per-router via `Security`/`Depends`
- Remove all `request.state` usage; inject typed values into handlers
- Introduce domain exception classes so error responses are thrown, not manually constructed
- Unify session lifecycle into two patterns: `Depends(get_session)` for requests, `get_background_session()` for non-request code
- Preserve identical external behavior (same endpoints, same error shapes, same status codes)

**Non-Goals:**
- Changing error response formats (OpenAI envelope, dashboard envelope stay as-is)
- Changing the authentication logic itself (bcrypt, TOTP, Bearer validation unchanged)
- Migrating the request decompression or request-id middleware (these are true cross-cutting concerns that belong in middleware)
- Refactoring the settings cache architecture (keeping `get_settings()` + `get_settings_cache()` split)
- Modifying the frontend

## Decisions

### D1: Auth as router-level dependencies, not middleware

**Decision**: Replace `add_auth_middleware()` with dependency functions. Each router group declares its own auth guard.

**Rationale**: FastAPI routes already define their path prefixes. Having middleware re-match the same paths is duplication. Router-level deps make auth requirements visible at the route definition and integrate with OpenAPI schema.

**Mapping:**

| Current middleware path match | New dependency | Applied to |
|---|----|---|
| `/v1/*`, `/backend-api/codex/*` | `validate_proxy_api_key()` → `Security` | `proxy_api.router`, `proxy_api.v1_router` |
| `/api/codex/usage` | `validate_codex_usage_identity()` → `Depends` | `proxy_api.usage_router` |
| `/api/*` (except `/api/dashboard-auth/*`) | `validate_dashboard_session()` → `Depends` | All dashboard module routers |

**Alternative considered**: Keep middleware but inject results via dependencies. Rejected because it still requires path matching and `request.state`.

### D2: Domain exceptions with registered handlers

**Decision**: Create exception classes in `app/core/exceptions.py` and register handlers in `app/core/handlers/exceptions.py`. Remove `api_errors.py` middleware.

**Exception hierarchy:**

```
AppError (base)
├── ProxyAuthError          → 401, OpenAI error envelope
├── ProxyModelNotAllowed    → 403, OpenAI error envelope
├── ProxyRateLimitError     → 429, OpenAI error envelope
├── DashboardAuthError      → 401, dashboard error envelope
├── DashboardNotFoundError  → 404, dashboard error envelope
└── DashboardConflictError  → 409, dashboard error envelope
```

**Rationale**: Centralizes error formatting in handlers instead of scattering `JSONResponse(content=openai_error(...))` across 40+ callsites. Exception handlers already exist for `RequestValidationError` and `StarletteHTTPException`; domain exceptions are a natural extension.

**Alternative considered**: Use FastAPI's `HTTPException` with typed `detail` dicts. Rejected because it conflates application errors with framework errors and requires casting in handlers.

### D3: Typed auth results as dependency return values

**Decision**: Auth dependencies return typed dataclasses/models directly. Handlers receive them as parameters.

```python
async def validate_proxy_api_key(
    authorization: Annotated[HTTPAuthorizationCredentials | None, Security(HTTPBearer(auto_error=False))],
) -> ApiKeyData | None:
    ...

@v1_router.post("/responses")
async def v1_responses(
    api_key: Annotated[ApiKeyData | None, Depends(validate_proxy_api_key)],
    ...
):
    ...
```

**Rationale**: Eliminates `request.state` and `getattr` patterns. Type checkers verify that handlers use the correct type. Dependencies are individually mockable in tests.

### D4: Two session patterns — request-scoped and background

**Decision**: Standardize on exactly two patterns:
1. **Request-scoped**: `Depends(get_session)` — already used by module contexts in `dependencies.py`
2. **Background**: `get_background_session()` async context manager — for schedulers, and auth dependencies that need DB access outside the request session

```python
# In db/session.py
@asynccontextmanager
async def get_background_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

**Rationale**: Auth dependencies run before the request handler, so they can't share the handler's request-scoped session. Using a separate short-lived session for auth validation is the correct pattern. Background schedulers use the same `get_background_session()`.

### D5: Path-agnostic exception handlers

**Decision**: Replace path-based branching in `exceptions.py` with format-aware handlers. Each domain exception carries its error format. For `RequestValidationError` and `StarletteHTTPException`, use a `request.state.error_format` flag set by a lightweight dependency on each router group.

```python
# Router-level dependency sets the error format
def set_openai_error_format(request: Request) -> None:
    request.state.error_format = "openai"

v1_router = APIRouter(dependencies=[Depends(set_openai_error_format)])
```

**Rationale**: Domain exceptions already know their format. For framework exceptions (validation, 404), the router-level marker eliminates path-string matching. Minimal `request.state` usage (single enum-like flag) is acceptable for cross-cutting error formatting.

**Alternative considered**: Separate exception handler registrations per sub-app. Rejected because FastAPI doesn't support per-router exception handlers natively.

## Risks / Trade-offs

- **Auth dependency creates a short-lived DB session per request** → Mitigation: Auth deps only query DB when `api_key_auth_enabled` is true (settings cache check first). Same as current middleware behavior.
- **Middleware removal is a large diff** → Mitigation: Phased approach — add dependencies first, verify tests pass, then remove middleware.
- **`request.state.error_format` is still a side-channel** → Acceptable trade-off: it's a single read-only flag set by a router dependency, not a mutable data pipe. Eliminates all path matching in handlers.
- **Exception handlers change error source from middleware to handler layer** → Mitigation: Error response shapes are identical. Integration tests verify status codes and payloads.

## Migration Plan

1. **Phase 1**: Add `app/core/exceptions.py` with domain exception classes. Register handlers. No behavior change yet.
2. **Phase 2**: Create auth dependency functions in `app/core/auth/dependencies.py`. Add `get_background_session()` to `db/session.py`. Wire dependencies onto routers.
3. **Phase 3**: Migrate route handlers from manual `JSONResponse` error returns to raising domain exceptions.
4. **Phase 4**: Remove `dashboard_auth.py` middleware, `api_errors.py` middleware, and path-based branching in exception handlers. Remove `request.state.api_key` usage.
5. **Phase 5**: Update tests to mock dependencies instead of middleware side-effects.
