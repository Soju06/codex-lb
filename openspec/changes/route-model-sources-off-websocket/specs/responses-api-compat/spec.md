## ADDED Requirements

### Requirement: Source-owned models are not served over the WebSocket transport

Model sources are reachable only from the HTTP request path. When a WebSocket
Responses session requests a model that resolves to an enabled,
Responses-capable OpenAI-compatible model source, the system SHALL NOT dispatch
the request to a subscription account.

The check SHALL be applied on the connect path before account selection, and
SHALL also be applied to every prepared `response.create`, so that a turn which
switches to a source-owned model on an already-open subscription upstream is
also rejected instead of being forwarded.

Both failures MUST use error code `model_source_requires_http_transport`. On the
connect path the failure MUST be emitted as a service-level connect failure
(HTTP status `503`), so that Codex clients fall back to the HTTP transport,
where source routing is applied. For a prepared `response.create` on an
established session the failure MUST be emitted as a terminal error for that
turn, and any usage reservation held for the turn MUST be released.

When source resolution is unavailable, the WebSocket transport MUST fall back to
subscription account selection rather than failing the request. The resolution
runs after a turn's usage reservation is acquired but before it is registered
for cleanup, so a propagating failure would end the session and strand the
reservation; the degraded behaviour is the pre-change one, where the
subscription upstream rejects the model. This applies to the WebSocket transport
only — the HTTP request path MUST continue to surface resolution failures, since
silently routing source traffic to a subscription account would be worse there.

#### Scenario: Source-owned model over WebSocket fails the connect

- **GIVEN** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a client opens a WebSocket Responses session requesting model `m`
- **THEN** the system fails the connect with error code `model_source_requires_http_transport`
- **AND** no subscription account is selected for the request

#### Scenario: Later turn switching to a source-owned model is rejected

- **GIVEN** a WebSocket Responses session already has an open subscription-account upstream
- **AND** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a subsequent `response.create` requests model `m`
- **THEN** the system emits a terminal error with code `model_source_requires_http_transport`
- **AND** the frame is not forwarded to the subscription account on the open upstream
- **AND** the turn's usage reservation is released

#### Scenario: An API key that enforces a source-owned model is rejected

- **GIVEN** an API key whose `enforced_model` resolves to an enabled model source
- **WHEN** the key opens a WebSocket Responses session requesting any model
- **THEN** the enforced model is resolved against the model sources
- **AND** the session fails with `model_source_requires_http_transport`

#### Scenario: Subscription models are unaffected

- **GIVEN** a model that is not served by any enabled model source
- **WHEN** a client opens a WebSocket Responses session requesting that model
- **THEN** account selection proceeds unchanged

#### Scenario: Source resolution failure falls back to subscription selection

- **GIVEN** the model-source catalog cannot be read
- **WHEN** a client opens a WebSocket Responses session
- **THEN** account selection proceeds as it did before the guard existed
- **AND** the session is not terminated by the resolution failure
