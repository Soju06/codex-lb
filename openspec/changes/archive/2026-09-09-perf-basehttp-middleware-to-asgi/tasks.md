## 1. Middleware

- [x] 1.1 Add `ImageRouteStartedAtMiddleware` (pure ASGI) in `app/core/handlers/exceptions.py`.
- [x] 1.2 Register it via `app.add_middleware` inside `add_exception_handlers`, keeping the slot between app-version and trusted-proxy-headers.
- [x] 1.3 Remove the `@app.middleware("http")` registration.

## 2. Tests

- [x] 2.1 Assert no `BaseHTTPMiddleware` entry remains in `create_app().user_middleware` and the middleware stack stays unbuilt.
- [x] 2.2 Assert image routes (all four paths, including `root_path`) receive a float start time via `scope["state"]` and `request.state`; other HTTP paths and WebSocket scopes are untouched.
- [x] 2.3 Assert pre-handler rejection observability consumes the ingress start time rather than the fallback.
- [x] 2.4 Assert forwarded ASGI messages are identical with and without the middleware (JSON and streaming).
- [x] 2.5 Pin that a mid-stream generator failure propagates without a synthetic terminal body chunk.

## 3. Verification

- [x] 3.1 Run the middleware ordering tests, image-route observability integration tests, SSE unit tests, ruff, ty, the architecture check, and OpenSpec validation.
- [x] 3.2 Re-run the BaseHTTPMiddleware vs pure-ASGI micro-benchmark and record the numbers in the PR body.
