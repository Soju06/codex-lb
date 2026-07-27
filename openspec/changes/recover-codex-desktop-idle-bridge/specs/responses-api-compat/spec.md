## MODIFIED Requirements

### Requirement: HTTP bridge streams emit downstream liveness frames while pending

When an HTTP bridge Responses request is waiting for upstream queue events, the system MUST emit a downstream SSE liveness frame at the configured `sse_keepalive_interval_seconds` interval so downstream clients do not disconnect before the upstream terminal frame arrives. The first generated liveness frame MUST be delayed until after the HTTP bridge startup-error probe window so a local startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response. Once a generated liveness frame is emitted, the stream MUST be considered started for later HTTP-error propagation decisions, so a subsequent upstream `response.failed` is forwarded in-stream instead of being raised as a startup HTTP error.

If the pending request already has a response id, the liveness frame MAY be a `response.in_progress` SSE event for that response id. Before a response id exists, a verified native Codex client on `/backend-api/codex/responses` MUST receive an event-bearing `codex.keepalive` JSON SSE frame even when payload-shape heuristics also require OpenAI-compatible response normalization, because comment-only frames do not reset the native client's parsed-event idle timer. Native identity MUST come from the existing native User-Agent or originator allowlist and MUST NOT be inferred from continuity headers.

Explicit OpenAI SDK fingerprint markers, including `x-stainless-*` headers or an OpenAI User-Agent, MUST retain precedence for heartbeat framing and MUST receive comment liveness. Public `/v1/responses` and other non-native OpenAI SDK streams MUST retain comment heartbeats before `response.created`; public stream normalization MUST preserve those comments and MUST drop `codex.*` liveness events from the OpenAI contract surface. Heartbeat selection MUST NOT disable authentication, payload validation, event normalization, fingerprint normalization, or routing policy.

#### Scenario: Native Desktop shape receives parsed-event liveness

- **GIVEN** Codex Desktop sends `POST /backend-api/codex/responses` with a verified native User-Agent or originator
- **AND** its OpenAI-compatible payload and `Accept` header also trigger SDK-compatible event normalization
- **WHEN** no upstream event arrives before a response id is known
- **THEN** the proxy emits an event-bearing `codex.keepalive` JSON SSE frame
- **AND** it preserves any required response-event normalization

#### Scenario: Explicit SDK marker retains comment liveness

- **GIVEN** a request to `/backend-api/codex/responses` carries an `x-stainless-*` header or OpenAI User-Agent
- **WHEN** its payload also resembles a native Codex request
- **THEN** the proxy emits an SSE comment heartbeat before `response.created`
- **AND** it does not expose `codex.*` vendor events to the SDK stream

#### Scenario: Public v1 route never exposes native vendor heartbeat

- **GIVEN** a request targets public `/v1/responses`
- **WHEN** the request is pending before `response.created`
- **THEN** periodic liveness uses OpenAI-contract-safe comment frames
- **AND** the first data event remains `response.created`

#### Scenario: First HTTP bridge keepalive is delayed past startup probe

- **GIVEN** an HTTP bridge request is waiting for upstream queue events
- **AND** `sse_keepalive_interval_seconds` is shorter than the bridge startup-error probe window
- **WHEN** no upstream event arrives before the configured keepalive interval
- **THEN** the first generated keepalive is not emitted until the startup-error probe window has elapsed
- **AND** a startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response before any keepalive commits the stream

#### Scenario: HTTP bridge keepalive commits stream for later response-failed events

- **GIVEN** an HTTP bridge request emits a generated keepalive as its first downstream chunk
- **WHEN** the next upstream event is a `response.failed` with an HTTP status override
- **THEN** the `response.failed` event is forwarded on the SSE stream
- **AND** it is not raised as a startup HTTP error after bytes have already been emitted

#### Scenario: Public Responses normalizer preserves comment keepalive blocks

- **WHEN** the public `/v1/responses` stream normalizer receives an SSE comment keepalive block before a terminal event
- **THEN** it forwards the comment keepalive block unchanged
- **AND** it continues normalizing the subsequent Responses events normally
