# Context: pure ASGI image-route start-time middleware

## Purpose

Performance refactor of the last `BaseHTTPMiddleware` in the HTTP stack. The
middleware body is trivial (path lookup + `time.perf_counter()`), but the
`BaseHTTPMiddleware` wrapper spawned the whole HTTP app in a child task and
relayed every response chunk through an anyio memory stream. Every other
middleware in `create_app()` was already converted to pure ASGI.

## Decisions

- `Request.state` is backed by `scope["state"]` (Starlette `Request.state`
  uses `scope.setdefault("state", {})`), so the middleware writes into that
  dict with `setdefault` and the three consumers
  (`app/modules/proxy/api.py`, `app/core/handlers/exceptions.py`,
  `app/core/middleware/required_capability_http.py`) keep using
  `getattr(request.state, IMAGE_ROUTE_STARTED_AT_STATE, None)` unchanged.
- Registration stays inside `add_exception_handlers` so `app/main.py` and the
  production ordering (trusted-proxy-headers -> image start time -> app-version
  -> ...) are untouched.
- `get_route_path(scope)` is used, as before, so a mounted `root_path` still
  matches the image routes.

## Accepted behavior change: mid-stream failure framing

With `BaseHTTPMiddleware`, a response body generator that raised after some
chunks had been sent caused the middleware to emit a synthetic
`http.response.body` with `more_body=False` and then re-raise. The client saw
a cleanly terminated (but truncated) body. With pure ASGI the exception
propagates to the server without that terminator; uvicorn closes the
transport, so the client observes a connection close instead of a clean
end-of-stream. This matches how every other converted middleware already
behaves for the paths it wraps and is pinned by
`tests/unit/test_image_route_started_at_middleware.py`.

Scheduling also changes: the app no longer runs in a child task, so client
disconnect cancellation propagates directly and contextvars set by the
request-id middleware remain visible for the whole response stream.

## Example

`POST /v1/images/edits` rejected with 413 before the handler runs still logs
`images_route_complete ... status=413 outcome=invalid_request duration_ms=...`
where the duration is measured from HTTP ingress, not from the moment the
exception handler ran.

## Measurement

Micro-benchmark (venv Python 3.14 + uvloop, ASGI app driven in-process, no
server): see the PR body for before/after numbers per request and per streamed
chunk. Production verification: `py-spy --gil` after deploy should show no
`starlette/middleware/base.py` frames.
