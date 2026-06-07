## ADDED Requirements

### Requirement: Equal idle and request-budget stream deadlines preserve idle classification
When the configured upstream stream idle timeout is equal to the proxy request budget, and an already-started streaming Responses body has had no upstream activity for the full shared window, the system MUST classify the timeout as `stream_idle_timeout` even if scheduler jitter observes the deadline after it has elapsed. When the request budget is strictly shorter than the stream idle timeout, when the generic total timeout fires before an upstream response has started, when the remaining request budget for the next read is shorter than a fresh idle window, or when a generic total timeout follows recent upstream body activity, the system MUST continue to classify the timeout as `upstream_request_timeout`.

#### Scenario: Direct HTTP stream body deadline tie is classified as idle
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **AND** the upstream HTTP response headers have been received
- **WHEN** reading the response body times out just after that shared deadline
- **THEN** the downstream failure event uses `error.code = "stream_idle_timeout"`
- **AND** the error message is `"Upstream stream idle timeout"`

#### Scenario: Pre-response total timeout remains request-timeout classified
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **WHEN** the generic request total timeout fires before an upstream response has started
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Direct HTTP total timeout after recent activity remains request-timeout classified
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **AND** an upstream HTTP response body chunk was received less than a full idle window ago
- **WHEN** the generic request total timeout fires at the request-budget deadline
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Shorter request budget remains request-timeout classified
- **GIVEN** `proxy_request_budget_seconds` is strictly shorter than `stream_idle_timeout_seconds`
- **WHEN** the request budget elapses before the idle timeout
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Owner-forward receive deadline tie is classified as idle
- **GIVEN** an HTTP bridge owner-forward stream has equal idle and request-budget deadlines
- **AND** the remaining request budget for the next read is at least a full idle window
- **WHEN** receiving the next upstream chunk times out at that shared deadline
- **THEN** the owner-forward timeout uses `error_code = "stream_idle_timeout"`

#### Scenario: Owner-forward shorter remaining budget is request-timeout classified
- **GIVEN** an HTTP bridge owner-forward stream has equal configured idle and request-budget deadlines
- **AND** the remaining request budget for the next read is shorter than a fresh idle window
- **WHEN** receiving the next upstream chunk times out at the request-budget deadline
- **THEN** the owner-forward timeout uses `error_code = "upstream_request_timeout"`

### Requirement: Multiplexed websocket timeout ties preserve younger pending requests
When an upstream websocket or HTTP bridge session has multiple pending Responses turns and the oldest pending turn reaches an equal idle/request-budget deadline, the system MUST NOT fail all pending turns solely because the equal deadline is classified as `stream_idle_timeout`. It MUST fail only pending turns whose own request budget has elapsed, and it MUST keep younger pending turns queued until their own terminal event or timeout.

#### Scenario: Equal deadline on oldest pending request does not fail younger sibling
- **GIVEN** two pending websocket Responses requests share an upstream session
- **AND** the oldest request has reached an equal idle/request-budget deadline
- **AND** the younger request still has request budget remaining
- **WHEN** the upstream receive watchdog fires
- **THEN** the timeout classification is `stream_idle_timeout`
- **AND** the fail-all-pending path is not used
- **AND** only the expired oldest request is failed
- **AND** the younger request remains pending

### Requirement: HTTP bridge streams emit downstream liveness frames while pending
When an HTTP bridge Responses request is waiting for upstream queue events, the system MUST emit a downstream SSE liveness frame at the configured `sse_keepalive_interval_seconds` interval so downstream clients do not disconnect before the upstream terminal frame arrives. The first generated liveness frame MUST be delayed until after the HTTP bridge startup-error probe window so a local startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response. Once a generated liveness frame is emitted, the stream MUST be considered started for later HTTP-error propagation decisions, so a subsequent upstream `response.failed` is forwarded in-stream instead of being raised as a startup HTTP error. If the pending request already has a response id, the liveness frame MAY be a `response.in_progress` SSE event for that response id. If no response id is known yet, the Codex CLI route MUST emit an ignored `codex.keepalive` SSE data event because comment-only frames do not reset the CLI's EventSource idle timer. Public `/v1/responses` stream normalization MUST preserve SSE comment keepalives instead of treating them as malformed data, and MUST drop `codex.*` liveness events from the public OpenAI SDK contract surface.

#### Scenario: HTTP bridge emits response in-progress keepalive after response id is known
- **GIVEN** an HTTP bridge request has a known response id
- **WHEN** no upstream event arrives before the SSE keepalive interval elapses
- **THEN** the downstream stream emits a `response.in_progress` event for that response id
- **AND** the request remains pending

#### Scenario: HTTP bridge emits Codex keepalive before response id is known
- **GIVEN** an HTTP bridge request does not yet have a response id
- **WHEN** no upstream event arrives before the SSE keepalive interval elapses
- **THEN** the downstream stream emits a `codex.keepalive` SSE data event
- **AND** the request remains pending

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
