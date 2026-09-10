# Change: Replace the last BaseHTTPMiddleware with a pure ASGI middleware

## Why

`add_exception_handlers` still registered the image-route start-time stamp via
`@app.middleware("http")`, which Starlette implements as `BaseHTTPMiddleware`:
a child task per request plus an anyio memory-object-stream hop for every
response chunk. Because it sits directly inside the trusted-proxy-headers
middleware, the entire HTTP application ran inside that child task. A 60 s
production GIL profile attributed roughly 3-4% of samples to this relay, most
of it on streaming `/v1` traffic where every SSE chunk paid two extra task
switches.

## What Changes

- Replace the decorator with `ImageRouteStartedAtMiddleware`, a pure ASGI
  middleware that writes the start timestamp into `scope["state"]` (the dict
  backing `Request.state`) and calls the downstream app in the same task.
- Register it at the same slot so the production middleware order is unchanged.
- Pin the accepted framing change: when a response body generator raises after
  chunks were already sent, the exception now reaches the ASGI server without a
  synthetic terminal `more_body=False` chunk (the server closes the connection).
- Forwarded bytes on success paths are unchanged and covered by a test.

## Impact

- Affected specs: `images-api-compat` (observability start time is captured at
  ingress; behavior preserved, now stated), `http-ingress-limits` (middleware
  relay and mid-stream failure framing).
- Affected code: `app/core/handlers/exceptions.py` and its tests.
- No settings, schema, or API surface changes.
