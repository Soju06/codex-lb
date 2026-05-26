## Why

Issue #267 reports that some OpenAI-compatible clients append `/v1/` to whatever the operator configured as the base URL. With codex-lb's natural base of `https://example.com/backend-api/codex`, those clients end up hitting `https://example.com/backend-api/codex/v1/models`, `…/v1/responses`, etc. None of those routes exist — codex-lb registers the OpenAI-style endpoints at `/v1/<rest>` (top-level) and the Codex backend endpoints at `/backend-api/codex/<rest>`, but not at the combined `/backend-api/codex/v1/<rest>` shape.

Because the registered routes are otherwise symmetrical (`router.get("/models")` + `v1_router.get("/models")`, `router.post("/responses")` + `v1_router.post("/responses")`, etc.), every `/backend-api/codex/v1/<rest>` request has a working counterpart at `/backend-api/codex/<rest>`. Today those requests 404 with no operator-facing signal as to why.

## What Changes

- Add a small ASGI path-rewrite middleware that, on every request whose path starts with `/backend-api/codex/v1/`, strips the redundant `/v1` segment in-place by mutating `scope["path"]` and `scope["raw_path"]` before routing. The exact `/backend-api/codex/v1` and `/backend-api/codex` paths (no trailing rest) are left alone — only the duplicated prefix on subpaths is collapsed.
- Register the middleware in the FastAPI lifespan so it runs before the existing API firewall and the proxy router. Idempotent: subsequent middleware / route handlers see the canonical `/backend-api/codex/<rest>` path the rest of the stack already understands.
- Add unit coverage for the rewrite helper (canonical/no-op/idempotent/edge cases) and integration coverage that issues a request to the duplicated path and asserts the canonical handler runs.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: routing surface now accepts the `/backend-api/codex/v1/<rest>` prefix as a transparent alias for `/backend-api/codex/<rest>`, so misbehaving clients that append `/v1/` to the configured base URL stop hitting 404s.

## Impact

- **Code**: `app/core/middleware/path_rewrite.py` (new), `app/core/middleware/__init__.py`, `app/main.py`.
- **Tests**: `tests/unit/test_path_rewrite_middleware.py` (new), `tests/integration/test_proxy_api_extended.py` (new aliasing case).
- **API surface**: no new endpoints; `/backend-api/codex/v1/<rest>` becomes a transparent alias for `/backend-api/codex/<rest>`. Top-level `/v1/<rest>` is unchanged.
- **Operational**: no migration. No new configuration. The middleware is unconditional and cheap (string prefix check).
