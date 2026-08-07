## ADDED Requirements

### Requirement: Source-owned models are not served over the WebSocket transport

Model sources are reachable only from the HTTP request path. When a WebSocket
Responses session requests a model that resolves to an enabled,
Responses-capable OpenAI-compatible model source, the system SHALL fail the
WebSocket connect before selecting a subscription account, and SHALL NOT
dispatch the request to a subscription account.

The failure MUST use error code `model_source_requires_http_transport` and MUST
be emitted as a service-level connect failure (HTTP status `503`), so that Codex
clients fall back to the HTTP transport, where source routing is applied.

#### Scenario: Source-owned model over WebSocket fails the connect

- **GIVEN** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a client opens a WebSocket Responses session requesting model `m`
- **THEN** the system fails the connect with error code `model_source_requires_http_transport`
- **AND** no subscription account is selected for the request

#### Scenario: Subscription models are unaffected

- **GIVEN** a model that is not served by any enabled model source
- **WHEN** a client opens a WebSocket Responses session requesting that model
- **THEN** account selection proceeds unchanged
