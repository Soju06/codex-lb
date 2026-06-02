## Context

codex-lb already adds some cross-cutting response headers through HTTP middleware, such as `x-request-id`. The requested version header has the same scope: it should apply broadly to HTTP responses regardless of whether the route returns JSON, a static file, or a handled framework/domain error.

The repo currently has two possible version sources: FastAPI app metadata in `app/main.py` and the package release version in `app/__init__.py`. The FastAPI metadata is stale (`0.1.0`), while the package version is the maintained release value.

## Goals / Non-Goals

**Goals:**

- Add `X-App-Version` to HTTP responses with status codes in the `200-499` range.
- Use the package release version as the header value.
- Cover success and handled client-error responses without touching individual route handlers.
- Preserve route-specific headers and avoid overwriting an explicit `X-App-Version` if one is ever set downstream.

**Non-Goals:**

- Changing response bodies or OpenAI/dashboard error envelope shapes.
- Adding the version header to `5xx` responses.
- Adding the version header to websocket handshakes, websocket frames, or other non-HTTP response paths.
- Normalizing or correcting the existing FastAPI app metadata version as part of this change.

## Decisions

### Add the header in global HTTP middleware

The change will add a dedicated HTTP middleware that runs after `call_next(request)` returns a response object. If the response status code is between `200` and `499`, inclusive of `200` and exclusive of `500`, the middleware will attach `X-App-Version`.

Rationale:

- A middleware is the narrowest way to cover JSON responses, `FileResponse`, and handled framework/domain errors with one change.
- It avoids duplicating header logic across route handlers and exception handlers.
- It naturally excludes websocket traffic because the middleware is HTTP-only.

Alternative considered:

- Attach the header inside each exception handler and selected routes. Rejected because it is easy to miss non-JSON responses and future routes.

### Source the header from `app.__version__`

The middleware will import `__version__` from `app` and use that value directly for `X-App-Version`.

Rationale:

- `app.__version__` is the maintained release value and matches the existing release/version tests.
- The FastAPI metadata version in `app/main.py` is stale and would expose incorrect runtime information.

Alternative considered:

- Reuse `app.version` from the FastAPI instance. Rejected because it currently reports the wrong version.

### Preserve explicit downstream overrides

The middleware will use `response.headers.setdefault("X-App-Version", __version__)` rather than overwriting the header unconditionally.

Rationale:

- This keeps the behavior safe if a future route or proxy path needs to set a more specific version header explicitly.

## Risks / Trade-offs

- [Unhandled exceptions bypass the intended status range] -> Cover a representative `5xx` path to prove the header is absent on server-error responses.
- [Route-specific header behavior regresses on static files or fallback 404s] -> Include at least one integration assertion against a non-JSON or framework-generated response path.
- [Future developers accidentally source the wrong version field] -> Keep the spec explicit that the runtime header value comes from the package release version.
