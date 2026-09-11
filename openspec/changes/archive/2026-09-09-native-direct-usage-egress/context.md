# Direct usage transport ownership

Python retains admission, resolved routes, retries and UsagePayload validation.
The existing Rust helper performs one HTTP attempt and owns its response body.
An explicit RetryClient remains authoritative for direct calls. Routed calls
continue through CodexClient, which already prefers native transport.

For example, a 503 response whose body never ends must not postpone the next
permitted GET: close that response and use the existing direct ExponentialRetry
delay (1, 2, then 2 seconds with current defaults). A final 503 with plain text
must preserve that text in UsageFetchError. A truncated 200 body must instead
remain a transport failure, never turn into an invalid-payload 502.

Only missing/unstartable helpers on the initial request permit Python fallback.
After a native attempt, a disappearing helper cannot reset the retry budget by
switching transports. Incompatible helpers fail before HTTP dispatch. Caller
cancellation cleans up its exchange without shutting down the shared helper.

The native request explicitly copies aiohttp's default Accept-Encoding value:
the helper only enables decompression for requests that negotiate it. Charset,
empty-body and JSON-syntax handling likewise follow the default Python session;
body transport failures remain outside the syntax fallback.

The validation uses synthetic loopback origins and disposable tokens. It proves
adapter/helper behavior, not production canary performance or upstream availability.

## Verification (2026-09-09)

- `CODEX_LB_NATIVE_EGRESS_TEST_BINARY=<built-helper> uv run pytest -q -ra
  tests/unit/test_usage_client.py tests/unit/test_usage_updater.py
  tests/unit/test_codex_client.py tests/unit/test_native_egress.py
  tests/unit/test_proxy_env.py tests/integration/test_native_routed_egress.py
  tests/integration/test_native_sse_egress.py
  tests/integration/test_native_websocket_events.py
  tests/integration/test_native_usage_egress.py`: 653 passed; one existing
  Starlette/AnyIO deprecation warning. The usage subset has 24 unit cases and
  24 actual-helper probes. Local Python is 3.14; CI uses Python 3.13.
- Gzip and deflate parity probes both failed before compression negotiation
  was added, then passed with the fix.
- `make rust-check`: format, Clippy, workspace tests and release build passed.
- `make lint`, `make typecheck`, `make package`: passed, including wheel assets.
- Strict change and all-main-spec validation passed before archive.
- Independent read-only review found retry/fallback behavior correct and
  identified the response encoding gap. The final review after the fix had
  no remaining actionable findings.

## Main integration (2026-09-10)

Integrated main through `ef1af872d`. The native request now uses the same fixed
`USAGE_FETCH_TIMEOUT_SECONDS` default as the Python and routed paths after main
removed the corresponding setting. The native selection test covers both an
omitted timeout and an explicit override. The scoped suite above passes 651
cases after main's test updates; lint, types and all 64 strict specs also pass.
