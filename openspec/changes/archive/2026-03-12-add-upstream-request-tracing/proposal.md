## Why

Operators can see persisted request-log rows in the dashboard, but the live server console still lacks two critical observability features:

- console lines are not guaranteed to include explicit timestamps
- outbound upstream requests and failure details are not logged clearly enough to explain what the proxy sent to the provider or why a 4xx/5xx happened

That makes it hard to correlate a client-visible failure with the exact upstream request, error message, and request id during debugging.

## What Changes

- Add timestamped console log formatting for runtime and access logs.
- Add configurable outbound upstream request tracing for proxy calls, including request start/completion and optional payload logging.
- Log 4xx/5xx proxy error responses with request id, status, code, and message so local server failures are visible in the console.

## Impact

- Code: `app/cli.py`, `app/core/clients/proxy.py`, `app/core/handlers/exceptions.py`, `app/modules/proxy/api.py`
- Tests: `tests/unit/test_cli.py`, `tests/unit/test_proxy_utils.py`
- Specs: `openspec/specs/proxy-runtime-observability/spec.md`
