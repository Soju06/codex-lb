### ADDED Requirements

### Requirement: HTTP /v1/responses preserves upstream websocket session continuity
When serving HTTP `/v1/responses`, the service MUST preserve upstream Responses websocket session continuity on a stable per-session bridge key instead of opening a brand new upstream session for every eligible request. The bridge key MUST use an explicit session/conversation header when present; otherwise it MUST use normalized `prompt_cache_key`, and when the client omits `prompt_cache_key` the service MUST derive a stable key from the same cache-affinity inputs already used for OpenAI prompt-cache routing. While bridged, the service MUST preserve the external HTTP/SSE contract, MUST continue request logging with `transport = "http"`, and MUST keep requests from different bridge keys isolated from one another.

#### Scenario: sequential HTTP responses requests reuse the same bridged upstream session
- **WHEN** a client sends repeated HTTP `/v1/responses` requests with the same stable bridge key
- **THEN** the service reuses one upstream websocket session for those requests instead of opening a fresh upstream session per request

#### Scenario: HTTP previous_response_id remains valid within a bridged session
- **WHEN** a client sends a later HTTP `/v1/responses` request with `previous_response_id` that references a response created earlier on the same bridged session
- **THEN** the service forwards that request through the same upstream websocket session so upstream can resolve the referenced prior response

#### Scenario: bridged HTTP requests keep external HTTP transport logging
- **WHEN** the service fulfills an HTTP `/v1/responses` request through an internal upstream websocket bridge
- **THEN** the persisted request log still records `transport = "http"`

#### Scenario: clean upstream close forces a fresh bridged session
- **WHEN** an existing bridged upstream websocket closes cleanly after prior HTTP `/v1/responses` work completes
- **THEN** the next HTTP `/v1/responses` request for that same bridge key opens a fresh upstream websocket session instead of reusing the closed session

#### Scenario: active bridge pool exhaustion fails fast without evicting live sessions
- **WHEN** the HTTP `/v1/responses` bridge pool has reached its configured maximum session count
- **AND** every existing bridge session still has pending in-flight requests
- **THEN** the service MUST NOT evict those active bridge sessions
- **AND** it MUST fail the new request fast with `429 rate_limit_exceeded`

#### Scenario: codex-session bridge sessions outlive prompt-cache sessions
- **WHEN** an HTTP `/v1/responses` bridge session is keyed by Codex turn/session affinity
- **THEN** the service applies the longer Codex bridge idle TTL instead of the generic prompt-cache TTL
- **AND** when idle eviction is required the service prefers evicting non-Codex prompt-cache bridge sessions before idle Codex-affinity bridge sessions

#### Scenario: optional Codex-affinity bridge prewarm stays behind an explicit flag
- **WHEN** an HTTP `/v1/responses` bridge session is keyed by Codex turn/session affinity
- **AND** Codex bridge prewarm is disabled
- **THEN** the first client-visible request is sent upstream directly without an extra internal warmup request

#### Scenario: enabled Codex-affinity bridge prewarm preserves the HTTP contract
- **WHEN** an HTTP `/v1/responses` bridge session is keyed by Codex turn/session affinity
- **AND** Codex bridge prewarm is enabled
- **AND** the first request on that session does not already reference `previous_response_id`
- **THEN** the service sends one internal `response.create` prewarm with `generate=false` before the client-visible request
- **AND** the client-visible response contract remains unchanged

#### Scenario: bridge enforces deterministic owner instance only for stable bridge keys
- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a stable bridge key derived from turn-state, session header, or prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service fails the request fast with `bridge_instance_mismatch`
- **AND** it MUST NOT create a fresh local bridge session for that key on the wrong instance

### Requirement: Websocket responses advertise and honor Codex turn-state affinity
When serving websocket Responses endpoints, the service MUST advertise an `x-codex-turn-state` header during websocket accept. If the client reconnects and presents that same `x-codex-turn-state`, the service MUST treat it as the highest-priority Codex-affinity key for upstream routing on that websocket turn. On `/v1/responses`, a proxy-generated turn-state MUST NOT override the first request's prompt-cache routing unless the client explicitly sends the turn-state back.

#### Scenario: backend websocket generates a turn-state for native Codex clients
- **WHEN** a client opens `/backend-api/codex/responses` without an existing `x-codex-turn-state`
- **THEN** the websocket accept response includes a generated non-empty `x-codex-turn-state`
- **AND** the proxy uses that same generated turn-state as the Codex session affinity key for the upstream websocket

#### Scenario: websocket reconnect honors client-provided turn-state
- **WHEN** a client opens a websocket Responses route and provides `x-codex-turn-state`
- **THEN** the websocket accept response echoes that same turn-state
- **AND** the proxy uses that same turn-state as the Codex session affinity key

### Requirement: Auto websocket fallback remains narrow and explicit
When automatic upstream transport selection prefers websocket, the service MUST only downgrade to HTTP automatically on `426 Upgrade Required`. Handshake failures such as `403 Forbidden` or `404 Not Found` MUST surface as upstream errors instead of silently falling back to HTTP.

#### Scenario: forbidden websocket handshake does not silently downgrade
- **WHEN** auto transport chooses websocket and upstream rejects the websocket handshake with `403`
- **THEN** the service returns an upstream error
- **AND** it MUST NOT retry the same request over HTTP automatically
