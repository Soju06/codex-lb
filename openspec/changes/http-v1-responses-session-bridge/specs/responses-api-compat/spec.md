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
