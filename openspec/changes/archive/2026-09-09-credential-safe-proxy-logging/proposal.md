## Why

Production logs show `ERROR asyncio Unclosed connection` lines whose aiohttp
`ConnectionKey` repr embeds the routed proxy URL with the plaintext proxy
password. The Codex upstream client passes the credential-bearing proxy URL
to aiohttp, which stores it verbatim in the connection pool key and renders it
in `Connection.__repr__` and `ClientHttpProxyError.__str__`; the loop's default
exception handler then logs that repr through the `asyncio` logger, and none
of the application log formatters redact URL userinfo.

## What Changes

- Routed aiohttp requests and websocket connects carry proxy credentials in a
  `Proxy-Authorization` header (latin1 Basic token, byte-identical to the one
  aiohttp derives from URL userinfo) and a credential-free proxy URL, so
  aiohttp repr surfaces never contain the password. Credentialed aiohttp routes
  require a TLS (`https`/`wss`) upstream target because aiohttp forwards proxy
  headers only on the CONNECT tunnel; plaintext targets fail closed for the
  whole ordered pool before any dispatch and ahead of every transport branch
  (native egress and SOCKS included), as a connect-phase transport error that
  callers map to the usual upstream-unavailable response. The resolver rejects
  usernames containing `:` (not encodable as Basic credentials). Native egress
  and SOCKS transports keep their existing fields.
- Every rendered log record (text and JSON formatters, any logger) masks
  `scheme://user:pass@` userinfo and `Basic <token>` values in the
  `Basic`/`basic`/`BASIC` spellings (the
  reversible token aiohttp reprs from the CONNECT `Proxy-Authorization`
  header); WARNING-and-higher records additionally get the existing
  keyed/bearer/basic/authorization/JSON secret patterns and secret-keyed
  structured extras (`password`, `*_token`, `api_key`, ...) of any value type,
  and structured extra keys are redacted like values. Redaction never
  raises, including for cyclic, pathologically deep, or unprintable structured
  extras. `log_error_response` also masks URL userinfo. The server entrypoint
  routes `warnings.warn` output through the same handlers.
- Out of scope here, stacked as the follow-up change
  `credential-safe-proxy-logging-loop-handler`: the redacting asyncio loop
  exception handler (defense in depth for object reprs), the dashboard
  colon-username hardening, and the Responses websocket `InvalidProxy`
  message.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-proxy-routing`: aiohttp routed egress MUST carry proxy credentials
  in `Proxy-Authorization`, never URL userinfo; credentialed aiohttp routes
  MUST require a TLS target.
- `proxy-runtime-observability`: rendered log records MUST redact URL
  userinfo and keyed secrets regardless of the originating logger, and
  redaction MUST never drop a record.

## Impact

- Code: `app/core/upstream_proxy/types.py`, `app/core/upstream_proxy/resolver.py`,
  `app/core/clients/codex.py`, `app/core/runtime_logging.py`, `app/cli.py`.
- Tests: `tests/unit/test_upstream_proxy_types.py` (new),
  `tests/unit/test_codex_client.py`, `tests/unit/test_structured_logging.py`,
  `tests/unit/test_upstream_proxy_resolver.py`, `tests/unit/test_cli.py`.
- Wire compatibility: identical CONNECT `Proxy-Authorization` bytes, identical
  per-proxy connection pooling (keyed through `proxy_headers_hash`), no
  forwarded payload change. INFO-level log records cost ~1 us more to render.
- No settings, dependencies, schemas, routes, database, or frontend changes.
