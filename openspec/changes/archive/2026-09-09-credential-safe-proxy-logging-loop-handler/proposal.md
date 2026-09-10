## Why

`credential-safe-proxy-logging` keeps proxy credentials out of aiohttp repr
surfaces and redacts every rendered log record. Three defense-in-depth gaps
remain around it: the asyncio/uvloop default loop exception handler renders
context values with `repr()` (aiohttp `ConnectionKey` proxy URLs, `BasicAuth`,
task exceptions) before any formatter runs, so a third-party or misconfigured
handler would still see the secret; the dashboard accepts proxy usernames
containing `:` that the resolver now rejects, turning the endpoint test route
into an unhandled 500 for already persisted rows; and the direct websocket
path forwards `websockets.InvalidProxy` text (the full proxy URL, userinfo
included) to API clients under the Responses policy.

## What Changes

- The application installs a redacting asyncio loop exception handler once at
  lifespan start. It copies the context, replaces every non-text value whose
  `repr()` changes under redaction with a stand-in carrying the redacted repr,
  and delegates to the previously installed (or default) handler, so
  secret-free contexts render byte-identically. Idempotent and fail-closed: a
  value whose `repr()` raises becomes an opaque stand-in, and any other failure
  delegates a context whose object values are all stand-ins, so the report is
  still emitted without rendering an unredacted value.
- The dashboard rejects proxy usernames containing `:` at endpoint creation
  with `invalid_proxy_username`, and the endpoint test route reports the
  resolver reason as a failed probe instead of an unhandled error.
- The direct websocket connector returns the fixed credential-safe message for
  `InvalidProxy` under every policy (Responses included) and logs only the
  URL-free websockets reason.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: application startup MUST install a redacting
  asyncio loop exception handler whose secret-free output is byte-identical to
  the default handler.
- `upstream-proxy-routing`: the dashboard MUST reject colon usernames at
  creation and the endpoint test route MUST report resolver rejections.
- `realtime-api-compat`: the Responses websocket `InvalidProxy` message is now
  the same fixed credential-safe message as the live sideband.

## Impact

- Depends on `credential-safe-proxy-logging` (`redact_rendered_log_text`, the
  resolver `invalid_proxy_username` rule); stacked on that branch.
- Code: `app/core/runtime_logging.py`, `app/main.py`,
  `app/modules/settings/api.py`, `app/core/clients/proxy_websocket.py`.
- Tests: `tests/unit/test_runtime_logging_loop_handler.py` (new),
  `tests/integration/test_settings_api.py`,
  `tests/unit/test_proxy_websocket_client.py`.
- Runs only on loop exception-handler invocations and failing configuration
  paths; no hot-path cost, no wire or payload change.
- No settings, dependencies, schemas, routes, database, or frontend changes.
