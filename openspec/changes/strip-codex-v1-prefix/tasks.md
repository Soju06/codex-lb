## 1. Path-Rewrite Middleware

- [x] 1.1 Add `app/core/middleware/path_rewrite.py` with an `add_backend_api_codex_v1_alias_middleware(app)` registrar and an internal `_canonicalize_backend_api_codex_path(path)` helper.
- [x] 1.2 Mutate `scope["path"]` and `scope["raw_path"]` together so downstream middleware/routers see the canonical path.
- [x] 1.3 Leave `/backend-api/codex/v1` (no rest) and `/backend-api/codex` alone; only collapse the `/backend-api/codex/v1/<rest>` prefix where `<rest>` is non-empty.
- [x] 1.4 Export the registrar from `app/core/middleware/__init__.py` and call it from `app/main.py` ahead of the existing api-firewall middleware.

## 2. Tests

- [x] 2.1 Unit tests for `_canonicalize_backend_api_codex_path`: canonical no-op, alias rewrite, idempotent re-application, edge paths (`/backend-api/codex`, `/backend-api/codex/v1`, trailing slash, `/v1/...` top-level).
- [x] 2.2 Integration test: `GET /backend-api/codex/v1/models` returns the same payload as `GET /backend-api/codex/models`.

## 3. Spec Delta

- [x] 3.1 Add `openspec/changes/strip-codex-v1-prefix/specs/responses-api-compat/spec.md` describing the alias requirement.
- [x] 3.2 `openspec validate --specs` (if available).
