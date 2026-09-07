## MODIFIED Requirements

### Requirement: Standalone Codex web search is forwarded faithfully

The proxy SHALL expose `POST /backend-api/codex/alpha/search` and
`POST /v1/alpha/search` through the same proxy-authenticated Codex control-request
path used by other unary Codex control endpoints. The existing doubled-prefix
rewrite MUST make `POST /backend-api/codex/v1/alpha/search` equivalent to the
canonical Codex route. All equivalent paths MUST apply the same required-capability
transport restrictions. The proxy MUST preserve the inbound request body and
query parameters, MUST apply the existing API-key scope, account selection,
token refresh, session affinity, failover, and upstream-route policies, and MUST
forward the request to the upstream `POST /codex/alpha/search` path. Successful
downstream responses MUST preserve the upstream status and body and MUST include
only response headers allowed by the existing Codex control-response policy.
Final non-2xx responses MUST preserve their status while using the existing
Codex control OpenAI error-envelope normalization. The proxy MUST NOT parse,
normalize, or invent a local schema for successful search requests or responses.

#### Scenario: authenticated standalone search reaches the upstream Codex path

- **GIVEN** a valid proxy API key and at least one eligible ChatGPT account
- **WHEN** Codex sends `POST /backend-api/codex/alpha/search`,
  `POST /v1/alpha/search`, or `POST /backend-api/codex/v1/alpha/search` with a body
  and query parameters
- **THEN** the proxy forwards the unchanged body and query parameters to
  `POST /codex/alpha/search` using the selected account credentials
- **AND** the downstream client receives the upstream status and body

#### Scenario: search aliases enforce proxy authentication

- **GIVEN** proxy API-key authentication is enabled
- **WHEN** a search request on any equivalent path has missing or invalid credentials
- **THEN** the proxy returns HTTP 401 with the existing OpenAI authentication envelope
- **AND** it does not forward the request upstream

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

- **WHEN** a client sends a non-POST request to any equivalent search path
- **THEN** the request does not enter the upstream search forwarding path

#### Scenario: trailing slashes retain the existing rejection contract

- **WHEN** a client sends POST to any equivalent search path with a trailing slash
- **THEN** the proxy returns HTTP 405 with the OpenAI error envelope
- **AND** it does not redirect or forward the request upstream
