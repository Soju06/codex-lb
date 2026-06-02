# api-response-metadata Specification

## Purpose
TBD - created by archiving change add-app-version-response-header. Update Purpose after archive.
## Requirements
### Requirement: HTTP 2xx-4xx responses include the running app version header
The system MUST attach `X-App-Version` to HTTP responses whose final status code is in the `200-499` range. The header value MUST equal the running codex-lb package version. This requirement applies across dashboard, health, proxy, and static-file HTTP routes, including handled framework or domain `4xx` responses. If a response already sets `X-App-Version`, the system MUST preserve the explicit value rather than overwrite it.

#### Scenario: Successful health response includes app version header
- **WHEN** a client sends `GET /health`
- **THEN** the response status is `200`
- **AND** the response includes `X-App-Version`
- **AND** the header value equals the running codex-lb package version

#### Scenario: Handled client error includes app version header
- **WHEN** a client sends a request that the service rejects with a handled `4xx` HTTP response
- **THEN** the response includes `X-App-Version`
- **AND** the header value equals the running codex-lb package version

#### Scenario: Existing explicit app version header is preserved
- **GIVEN** an HTTP response already sets `X-App-Version`
- **WHEN** the global response metadata policy runs
- **THEN** the existing `X-App-Version` value is preserved unchanged

### Requirement: HTTP 5xx and websocket responses omit the app version header
The system MUST NOT add `X-App-Version` to HTTP responses whose final status code is `500` or greater. The system MUST NOT add this header to websocket accept paths or websocket message traffic.

#### Scenario: Server error response omits app version header
- **WHEN** a request ends in an HTTP `503` response
- **THEN** the response does not include `X-App-Version`

#### Scenario: Websocket route does not gain app version response metadata
- **WHEN** a client connects to a websocket route
- **THEN** the global HTTP app-version response-header policy does not apply to websocket traffic

