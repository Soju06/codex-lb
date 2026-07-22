## ADDED Requirements

### Requirement: Standalone Codex web search is forwarded faithfully

The proxy SHALL expose `GET` and `POST /backend-api/codex/alpha/search` through
the same proxy-authenticated Codex control-request path used by other unary
Codex control endpoints. The proxy MUST preserve the inbound request body and
query parameters, MUST apply the existing API-key scope, account selection,
token refresh, session affinity, failover, and upstream-route policies, and MUST
forward the request to the upstream `/codex/alpha/search` path using the same
HTTP method. Successful downstream responses MUST preserve the upstream status
and body and MUST include only response headers allowed by the existing Codex
control-response policy. Final non-2xx responses MUST preserve their status
while using the existing Codex control OpenAI error-envelope normalization. The
proxy MUST NOT parse, normalize, or invent a local schema for successful search
requests or responses.

#### Scenario: authenticated standalone search reaches the upstream Codex path

- **GIVEN** a valid proxy API key and at least one eligible ChatGPT account
- **WHEN** Codex sends `POST /backend-api/codex/alpha/search` with a JSON body and
  query parameters
- **THEN** the proxy forwards the unchanged body and query parameters to
  `POST /codex/alpha/search` using the selected account credentials
- **AND** the downstream client receives the upstream status and body

#### Scenario: authenticated standalone search forwards GET query requests

- **GIVEN** a valid proxy API key and at least one eligible ChatGPT account
- **WHEN** Codex sends `GET /backend-api/codex/alpha/search` with query
  parameters
- **THEN** the proxy forwards the unchanged query parameters to
  `GET /codex/alpha/search` using the selected account credentials
- **AND** the downstream client receives the upstream status and body

#### Scenario: browser preflight succeeds locally for the allowed origin

- **WHEN** a browser sends `OPTIONS /backend-api/codex/alpha/search` with
  `Origin: https://chatgpt.com` and an access-control request method of `GET` or
  `POST`
- **THEN** the proxy returns HTTP 204 locally
- **AND** the response includes `Access-Control-Allow-Origin:
  https://chatgpt.com`, `Vary: Origin`, `Access-Control-Allow-Methods: GET,
  POST, OPTIONS`, and the requested access-control headers
- **AND** the proxy does not forward the preflight upstream or require proxy API
  key authentication for the preflight

#### Scenario: unsafe upstream response headers are not exposed

- **WHEN** the upstream search response includes both allowlisted metadata and
  a response header outside the Codex control-response allowlist
- **THEN** the proxy returns the allowlisted metadata
- **AND** it omits the non-allowlisted response header

#### Scenario: final upstream search failures use the control error contract

- **WHEN** upstream search failure handling finishes with a non-2xx response
- **THEN** the proxy preserves the final HTTP status
- **AND** it returns the failure through the existing OpenAI error envelope
- **AND** existing account refresh, health, and failover handling remains active

#### Scenario: unsupported methods do not enter search forwarding

- **WHEN** a client sends a non-GET, non-POST, non-OPTIONS request to
  `/backend-api/codex/alpha/search`
- **THEN** the request does not enter the upstream search forwarding path
