## Why

Outbound OAuth connection failures escape as unhandled HTTP 500s in both
account-add flows. On the direct-egress path `exchange_authorization_code`,
`request_device_code`, and `exchange_device_token` call
`aiohttp.ClientSession.post` themselves, so a transport failure
(`aiohttp.ClientError` such as `ClientConnectorError`, or `asyncio.TimeoutError`)
is not translated into the module's own `OAuthError`. The routed path already
does this through `_codex_post` / `CodexTransportError`.

The consequences are user-visible:

- The loopback callback handler catches `OAuthError`, `ReauthSeatMismatchError`
  and `AccountIdentityConflictError`, so a bare transport error escapes to
  aiohttp's default 500 page, the flow is never finalized, and `/api/oauth/status`
  keeps reporting `pending` — the dashboard polls forever.
- `POST /api/oauth/start` with `force_method=device` reaches the generic 500
  handler instead of the repo's dashboard error envelope.
- The device-token poll task dies with an unretrieved exception and leaves the
  flow `pending` instead of finalizing it as `error`.

## What Changes

- Translate transport failures raised by the three direct-egress OAuth posts into
  `OAuthError` with the existing `transport_error` code, at the single shared
  boundary the posts now pass through (`_post_auth_request`, mirroring
  `_codex_post`).
- Use a fixed, actionable, credential-free message — "Could not reach the OpenAI
  auth server; check network/proxy egress" — and log the exception class only,
  because an aiohttp transport message can embed the request URL and any proxy
  credentials it carries.
- Keep existing status-code, payload-validation, and pending-device handling
  untouched: HTTP failures, invalid payloads, `404 device_auth_unavailable`, and
  `authorization_pending` / `slow_down` keep their current behavior.
- Zero configuration: no new setting, dependency, route, schema, or migration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: require the OAuth client to map direct-egress transport
  failures to a sanitized `transport_error` `OAuthError`.
- `oauth-callback-privacy`: require the browser callback (and the dashboard
  start/status contract around it) to finalize a reachability failure as a usable
  error without emitting codes, state tokens, verifiers, tokens, or proxy
  credentials.

## Impact

- `app/core/clients/oauth.py`: new `_post_auth_request` async context manager,
  `OAUTH_TRANSPORT_ERROR_CODE` / `OAUTH_TRANSPORT_ERROR_MESSAGE`, and three call
  sites switched from `client_session.post(...)` to the wrapper.
- `tests/integration/test_oauth_flow.py`: three product-path regression tests
  (browser callback, device `start` envelope, device-token poll), which patch only
  the leased outbound HTTP session.
- No change to `app/modules/oauth/service.py` or `app/modules/oauth/api.py`: the
  existing `OAuthError` handling in the callback handler and the 502 dashboard
  envelope already do the right thing once the client raises `OAuthError`.
