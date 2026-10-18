## ADDED Requirements

### Requirement: Direct-egress OAuth transport failures surface as a sanitized OAuthError

The OAuth client SHALL translate an outbound transport failure raised while performing a direct-egress authorization-code exchange, device-code request, or device-token poll into `OAuthError` with the existing `transport_error` code. A transport failure is an `aiohttp.ClientError` (connector, proxy, TLS, or response-read failure) or an `asyncio.TimeoutError` raised around that request; it MUST NOT escape the OAuth client as an unhandled exception, and the client MUST NOT catch anything broader except that.

The translated error MUST carry a fixed, actionable, credential-free message telling the operator the auth server could not be reached and that network or proxy egress is the thing to check. Neither the error message nor the log record for that failure MAY include text taken from the transport exception, because an `aiohttp` transport message can embed the request URL and any proxy credentials it carries; the client SHALL log the exception class instead.

HTTP status failures, invalid or missing payload fields, the `device_auth_unavailable` 404 signal, and the `authorization_pending` / `slow_down` pending signals MUST retain their existing handling and error codes.

#### Scenario: Unreachable auth host during the token exchange

- **GIVEN** a direct-egress authorization-code exchange whose `POST` fails before any response with `aiohttp.ClientConnectorError`
- **WHEN** the exchange is attempted
- **THEN** it raises `OAuthError` with code `transport_error` and the fixed reachability message
- **AND** the raised error and the emitted log record contain no URL, host, proxy credential, authorization code, or code verifier from the request

#### Scenario: Timeout during the device-token poll

- **GIVEN** a direct-egress device-token poll whose `POST` raises `asyncio.TimeoutError`
- **WHEN** the poll is attempted
- **THEN** it raises `OAuthError` with code `transport_error` instead of letting the timeout escape
- **AND** the HTTP-status, payload-validation, and pending-device behavior of the poll is unchanged

#### Scenario: Routed and direct egress share one error contract

- **GIVEN** the same transport failure occurring on the routed egress path
- **WHEN** the routed exchange fails
- **THEN** it still raises `OAuthError` with the same `transport_error` code through the existing `_codex_post` translation
