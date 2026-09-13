## ADDED Requirements

### Requirement: A disabled model source refuses its models instead of falling through

The system SHALL NOT dispatch to a subscription account a request whose model
is served by an OpenAI-compatible model source that an operator has switched
off. It SHALL refuse such a request with HTTP status `503` and error code
`model_source_disabled`.

"Switched off" covers both a disabled source row and a disabled model row on an
enabled source. The refusal SHALL apply on `/v1/chat/completions`,
`/v1/responses`, and `/backend-api/codex/responses`.

The refusal SHALL be decided by the ordinary source-selection rules with the
enabled-state filter inverted and nothing else changed: same candidate list
(raw client alias and normalized model), same API key model allowlist, same
source assignment scope, same subscription-registry precedence, same route
shape, same streaming requirement. A request that the ordinary lookup would
have missed for any reason other than enabled state MUST keep its existing
behaviour, including a model no source exposes, a source the API key is not
assigned to, a chat-only source asked for a Responses route, and a
subscription-registry slug that an unscoped API key never source-routes.

Requests excluded from source routing — a terminal `compaction_trigger`, and
Responses requests pinned to the subscription account that received an uploaded
file — MUST NOT be refused, and MUST proceed to subscription routing as before.

The WebSocket transport cannot forward to a model source, so its
source-ownership guards SHALL treat a model owned only by a switched-off source
as source-owned: the turn fails with the existing service-level
`model_source_requires_http_transport` refusal instead of dispatching to a
subscription account, and the client's HTTP fallback then meets the
`model_source_disabled` refusal above. The guards' existing exclusions — a
structurally excluded request and a recorded previous-response subscription
owner — keep bypassing the guard unchanged.

The refusal MUST happen before any usage reservation is taken, so a refused
request strands no reservation, and MUST NOT create a request log entry for a
dispatch that never happened.

#### Scenario: Chat request for a disabled source's model is refused

- **GIVEN** an OpenAI-compatible model source exposes model `m` and is disabled
- **WHEN** a client calls `POST /v1/chat/completions` with model `m`
- **THEN** the response is `503` with error code `model_source_disabled`
- **AND** no subscription account is selected for the request
- **AND** no usage reservation is left held

#### Scenario: Responses request for a disabled source's model is refused

- **GIVEN** a Responses-capable OpenAI-compatible model source exposes model `m` and is disabled
- **WHEN** a client calls `POST /v1/responses` or `POST /backend-api/codex/responses` with model `m`
- **THEN** the response is `503` with error code `model_source_disabled`
- **AND** no subscription account is selected for the request

#### Scenario: A disabled model on an enabled source is refused

- **GIVEN** an enabled OpenAI-compatible model source whose model row for `m` is disabled
- **WHEN** a client calls `POST /v1/chat/completions` with model `m`
- **THEN** the response is `503` with error code `model_source_disabled`

#### Scenario: A model no source exposes is unaffected

- **GIVEN** no model source exposes model `m`, enabled or disabled
- **WHEN** a client calls `POST /v1/chat/completions` with model `m`
- **THEN** subscription routing proceeds exactly as it did before this requirement

#### Scenario: A WebSocket turn for a disabled source's model bounces to HTTP

- **GIVEN** a Responses-capable OpenAI-compatible model source exposes model `m` and is disabled
- **WHEN** a client requests model `m` over the WebSocket transport, at connect time or on a later turn over an already-open socket
- **THEN** the turn is refused with the service-level `model_source_requires_http_transport` failure that makes Codex clients retry over the HTTP transport
- **AND** the turn is not forwarded to a subscription account upstream

#### Scenario: A subscription slug shadowed by a disabled source is unaffected

- **GIVEN** a disabled OpenAI-compatible model source lists a slug the subscription model registry already serves
- **AND** an API key without source assignment scoping
- **WHEN** the key requests that slug
- **THEN** the request is not refused with `model_source_disabled`
- **AND** subscription routing proceeds unchanged
