# Context

## Purpose

Both OAuth account-add flows (browser callback server and device code) talk to the
OpenAI auth server through the OAuth client's direct-egress `POST`. A reachability
failure there is not a programming error: it is the ordinary case of a host with no
internet, a blocked egress, a dead proxy pool endpoint, or a slow auth server. It
must surface as an OAuth failure the operator can act on, never as an unhandled 500
and never as a flow that stays `pending` forever.

## Decisions

- **Fix at the client, not in the handlers.** `app/modules/oauth/api.py` already maps
  `OAuthError` to the dashboard envelope (502), and `app/modules/oauth/service.py`
  already finalizes both flows on `OAuthError`. Translating at the client means the
  callback page, the start envelope, the status endpoint, and the device poller all
  inherit one behavior with no per-handler duplication — and future OAuth call sites
  get it for free.
- **One shared boundary.** The three posts route through `_post_auth_request`, the
  direct-egress twin of the existing `_codex_post` (which already raises
  `OAuthError("transport_error", ...)` for routed `CodexTransportError`). The error
  code is the file's existing `transport_error`.
- **Fixed message, class-only logging.** `aiohttp` transport exception text embeds
  the target URL, and a proxy URL can carry credentials; the response body and the
  log line therefore carry no text from the exception. The full failure still reaches
  operators through the exception class and the request id.
- **The cause chain stays, but stays internal.** `OAuthError` is raised `from exc`, so
  the original aiohttp exception remains reachable through `__cause__` for debugging.
  It may embed the request URL and proxy credentials, so it must never be rendered to
  clients or logged with a full traceback at this boundary; the current handlers log
  only the class name.
- **Catch `aiohttp.ClientError` and `asyncio.TimeoutError` only.** Not `Exception`:
  a programming error inside the exchange must stay a 500, and
  `asyncio.CancelledError` (a `BaseException`) must keep propagating.
- **No new knob.** A timeout already exists (`OAUTH_TIMEOUT_SECONDS`); there is
  nothing for an operator to configure to make an unreachable host reachable.

## Failure modes considered

- Response body read failure (`aiohttp.ClientPayloadError`) after headers: also a
  transport failure, and it maps the same way because it surfaces inside the leased
  request context.
- Both flows racing the same single-use code: unchanged. The sanitized message goes
  through the existing `_finalize_callback_error` / `_set_error` monotonic guard, so a
  loser still honors a durable `success` instead of overwriting it with `error`.

## Example

Operator enables account proxy binding, the default pool's only endpoint is down, and a browser `POST /api/oauth/start` starts a browser flow. The auth redirect comes back with a valid `code` and `state`; `exchange_authorization_code` cannot connect through the pool endpoint. Before this change the callback server answered aiohttp's stock 500 page and `/api/oauth/status` stayed `pending` until the flow TTL. After it, the callback renders "Login failed — Could not reach the OpenAI auth server; check network/proxy egress", the durable flow status is `error` with that same message, the status poll stops, and the logs show one
`OAuth transport failure request_id=... exception_type=ClientConnectorError` line
without the URL, the pool endpoint, the authorization code, or the PKCE verifier.

Reproduction needs no production credentials: the regression tests patch only the
leased outbound HTTP session of `app.core.clients.oauth` so the real client
functions, error mapping, service, and route handlers run against a synthetic
`ClientConnectorError`.
