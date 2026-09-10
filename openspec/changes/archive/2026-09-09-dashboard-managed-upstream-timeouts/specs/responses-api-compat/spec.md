## MODIFIED Requirements

### Requirement: Long Codex websocket turns tolerate extended upstream silence
The default compact request budget MUST be at least 180 seconds, and the default upstream stream idle timeout MUST be at least 600 seconds, so long-running Codex turns can survive expensive compaction or tool execution without a local proxy watchdog ending the turn prematurely. Responses streams over both HTTP and WebSocket transports MUST use `http_responses_stream_request_budget_seconds` when it is configured; they MUST fall back to `proxy_request_budget_seconds` only when no stream-specific budget is available.

`compact_request_budget_seconds`, `stream_idle_timeout_seconds` and `proxy_request_budget_seconds` are dashboard-managed (`configuration-tiers`): each has a nullable `dashboard_settings` column of the same name whose non-NULL value MUST override the process environment value, which in turn overrides the code default. Consumers MUST read the effective value from the `SettingsCache` snapshot bound at the request or WebSocket entry point and MUST NOT query the database per request or per event. The environment variables remain as deprecated fallbacks and MUST NOT be copied into the column by the server; a client that echoes the effective values of a `GET /api/settings` response back through `PUT` stores them as explicit dashboard values (clients MUST send only the fields they intend to change). `GET /api/settings` MUST report any environment value the `Settings` model accepts, including one outside the bounds `PUT` enforces.

#### Scenario: compact and stream watchdog defaults leave room for long turns
- **WHEN** the service starts with default configuration
- **THEN** `compact_request_budget_seconds` is at least 180 seconds
- **AND** `stream_idle_timeout_seconds` is at least 600 seconds

#### Scenario: WebSocket Responses stream uses the stream-specific request budget
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **WHEN** a native WebSocket Responses stream computes its request deadline
- **THEN** the stream budget is 7200 seconds
- **AND** the generic 600 second proxy request budget does not terminate the turn

#### Scenario: WebSocket reconnect keeps the stream-specific deadline
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **AND** a native WebSocket Responses request needs to reconnect after more than 600 seconds but less than 7200 seconds
- **WHEN** the reconnect performs account selection and opens its replacement upstream WebSocket
- **THEN** both operations remain bounded by the original 7200-second stream deadline
- **AND** the reconnect does not fail solely because the generic 600-second budget elapsed

#### Scenario: Dashboard value overrides startup environment
- **GIVEN** `CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS=600` in the process environment and an operator has stored `900` for `proxy_request_budget_seconds` through `PUT /api/settings`
- **WHEN** a new request computes its request deadline on any replica
- **THEN** the deadline uses the 900 second dashboard value
- **AND** `GET /api/settings` reports `proxyRequestBudgetSeconds: 900` with `provenance.proxy_request_budget_seconds.source = "dashboard"`

#### Scenario: Clearing the dashboard value returns to the environment
- **GIVEN** the same deployment
- **WHEN** the operator sends `PUT /api/settings` with `proxyRequestBudgetSeconds: null`
- **THEN** the column becomes NULL, new requests use the 600 second environment value, and the provenance source becomes `"env"` (or `"default"` when the environment matches the code default)

#### Scenario: Timeout invariants are enforced on the effective values
- **GIVEN** the environment-only admission wait is 10 seconds
- **WHEN** the operator sends `PUT /api/settings` with `proxyRequestBudgetSeconds: 5`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `admission-wait-within-proxy-budget`
- **AND** a request that raises every violated budget in the same `PUT` is accepted

### Requirement: HTTP bridge streams emit downstream liveness frames while pending

When an HTTP bridge Responses request is waiting for upstream queue events, the system MUST emit a downstream SSE liveness frame at the configured `sse_keepalive_interval_seconds` interval so downstream clients do not disconnect before the upstream terminal frame arrives. The interval is dashboard-managed: a non-NULL `dashboard_settings.sse_keepalive_interval_seconds` MUST override the environment value, `0` disables generated liveness frames, and the value MUST be read from the `SettingsCache` snapshot bound to the request rather than from the environment alone. The first generated liveness frame MUST be delayed until after the HTTP bridge startup-error probe window so a local startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response. Once a generated liveness frame is emitted, the stream MUST be considered started for later HTTP-error propagation decisions, so a subsequent upstream `response.failed` is forwarded in-stream instead of being raised as a startup HTTP error. If the pending request already has a response id, the liveness frame MAY be a `response.in_progress` SSE event for that response id. If no response id is known yet, the Codex CLI route MUST emit an ignored `codex.keepalive` SSE data event because comment-only frames do not reset the CLI's EventSource idle timer. Public `/v1/responses` stream normalization MUST preserve SSE comment keepalives instead of treating them as malformed data, and MUST drop `codex.*` liveness events from the public OpenAI SDK contract surface.

Before a response id exists, a verified native Codex client on `/backend-api/codex/responses` MUST receive an event-bearing `codex.keepalive` JSON SSE frame even when payload-shape heuristics also require OpenAI-compatible response normalization, because comment-only frames do not reset the native client's parsed-event idle timer. Native identity MUST come from the existing native User-Agent or originator allowlist and MUST NOT be inferred from continuity headers. Explicit OpenAI SDK fingerprint markers, including `x-stainless-*` headers or an OpenAI User-Agent, MUST retain precedence for heartbeat framing and MUST receive comment liveness. Public `/v1/responses` and other non-native OpenAI SDK streams MUST retain comment heartbeats before `response.created`. Heartbeat selection MUST NOT disable authentication, payload validation, event normalization, fingerprint normalization, or routing policy.

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

#### Scenario: Dashboard keepalive interval overrides startup environment
- **GIVEN** the process environment leaves `sse_keepalive_interval_seconds` at 10 and an operator stores `0.5` through `PUT /api/settings`
- **WHEN** a Responses stream waits for its first upstream event on any replica
- **THEN** liveness frames are emitted every 0.5 seconds
- **AND** the request did not read `dashboard_settings` from the database beyond the cached snapshot

### Requirement: Responses upstream websocket liveness is bounded

The proxy MUST configure direct and routed upstream Responses WebSocket transports with finite ping/pong liveness detection derived from `proxy_downstream_websocket_idle_timeout_seconds`, read as the effective dashboard-managed value (a non-NULL `dashboard_settings.proxy_downstream_websocket_idle_timeout_seconds` overrides the environment value) from the snapshot bound to the connection. A direct connection MUST use the native helper watchdog when native egress is selected and the Python `websockets` watchdog only on the pre-dispatch missing-helper fallback. When an established Responses WebSocket is terminated because its transport did not receive the required pong, the adapter MUST classify the failure as `upstream_websocket_liveness_timeout`. Direct WebSocket and HTTP bridge relay owners MUST treat that failure as account neutral, MUST NOT transparently replay a pending request whose delivery is ambiguous, MUST finalize its pending request ownership exactly once, and MUST retire the affected upstream socket so a later client retry opens a fresh connection. An HTTP bridge reader MUST suppress its own pending-deque settlement only when a concurrent submitter explicitly claimed liveness-settlement ownership under the session lifecycle lock; `session.closed` alone MUST NOT suppress settlement.

#### Scenario: Direct Responses websocket loses pong liveness

- **GIVEN** a direct upstream Responses WebSocket has been established
- **WHEN** the selected native-helper or Python fallback keepalive watchdog terminates it after a pong timeout
- **THEN** the pending request fails with `upstream_websocket_liveness_timeout`
- **AND** the request is not transparently replayed
- **AND** the selected account receives no failure-health signal
- **AND** the affected upstream socket is retired

#### Scenario: Routed Responses websocket loses pong liveness

- **GIVEN** a routed upstream Responses WebSocket has been established for an HTTP bridge or direct WebSocket client
- **WHEN** the aiohttp heartbeat watchdog terminates it after a pong timeout
- **THEN** the pending request fails with `upstream_websocket_liveness_timeout`
- **AND** the request is not transparently replayed
- **AND** the selected account receives no failure-health signal
- **AND** the affected upstream socket is retired

#### Scenario: Long turn remains healthy through control frames

- **GIVEN** a Responses turn emits no application event within the liveness interval
- **WHEN** the upstream WebSocket continues replying to transport pings
- **THEN** the proxy keeps the upstream socket open
- **AND** the existing Responses request budget remains authoritative for the turn

#### Scenario: Closed bridge without a sender claim later loses pong liveness

- **GIVEN** an HTTP bridge session has multiple pending requests
- **AND** a separate submit failure marks the session closed without claiming liveness-settlement ownership
- **WHEN** the still-running upstream transport later expires its heartbeat
- **THEN** the reader settles every pending request with `upstream_websocket_liveness_timeout`
- **AND** the selected account receives no failure-health signal

#### Scenario: Claimed bridge settlement survives submitter cancellation

- **GIVEN** an HTTP bridge submitter claims liveness-settlement ownership after its send fails
- **WHEN** the submitter is cancelled before whole-deque settlement completes
- **THEN** settlement continues until every pending sibling is finalized exactly once
- **AND** the submitter cancellation is preserved after settlement completes

#### Scenario: Dashboard idle timeout controls new connections

- **GIVEN** `CODEX_LB_PROXY_DOWNSTREAM_WEBSOCKET_IDLE_TIMEOUT_SECONDS=120` and an operator stores `45` through `PUT /api/settings`
- **WHEN** a new downstream WebSocket connection is accepted on any replica
- **THEN** its idle timeout and the derived upstream ping/pong liveness window use 45 seconds
- **AND** connections accepted before the change keep the value they were bound with
