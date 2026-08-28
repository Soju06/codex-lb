# responses-api-compat Specification

## Purpose

Define Responses API compatibility contracts so Codex, OpenCode, and OpenAI-style clients preserve expected behavior.
## Requirements
### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as the bounded upstream account affinity key for prompt-cache correctness even when a `session_id` header is present. OpenAI-style route wiring MUST NOT upgrade those requests to durable `CODEX_SESSION` affinity by default. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: OpenAI-style route ignores session header for durable codex-session pinning
- **WHEN** a client sends `/v1/responses` or `/v1/responses/compact` with a non-empty `session_id` header and no explicit sticky-thread mode
- **THEN** the service does not persist a durable `codex_session` mapping solely from that header
- **AND** bounded prompt-cache affinity behavior remains in effect

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

### Requirement: Responses requests reject uploaded input_image references

The system SHALL accept `{"type":"input_file","file_id":"file_*"}` attached-file items in `/v1/responses`, `/backend-api/codex/responses`, and `/responses/compact` request payloads and forward them verbatim.

When an `input_image` part contains a `file_id` field or an `image_url` starting with `sediment://`, the proxy MUST return HTTP 400 with `error.code = "unsupported_input_image_format"` and an explanation that the upstream Responses API only accepts inline `data:` URLs for `input_image`. The proxy MUST NOT fetch the upload, MUST NOT inline-convert the image, and MUST NOT trim, slim, or rewrite any conversation content.

`app/core/openai/requests.py::extract_input_image_file_references` MAY be used to detect the unsupported shape. This request path MUST NOT fetch uploads, inline-convert images, or otherwise reshape inbound conversation payloads.

#### Scenario: input_image file_id is rejected before forwarding

- **WHEN** a `/v1/responses` request contains `{"type":"input_image","file_id":"file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** the response explains that inline `data:` URLs are the supported `input_image` contract

#### Scenario: sediment upload URL is rejected before forwarding

- **WHEN** a `/responses/compact` request contains `{"type":"input_image","image_url":"sediment://file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** does not fetch or inline-convert the upload

#### Scenario: large request payload routes via HTTP transport on auto

- **GIVEN** `upstream_stream_transport` is `"auto"` and the request payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large request payload bypasses the HTTP responses bridge

- **GIVEN** the HTTP responses bridge is enabled and the request payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Oversized responses request payloads fall back to HTTP
When `upstream_stream_transport` is `"auto"` and the serialized request payload size exceeds the WebSocket frame budget, the proxy MUST use upstream HTTP `POST` instead of WebSocket. If the HTTP responses bridge is enabled and the same oversized request would otherwise route through the bridge, the proxy MUST bypass the bridge for that request only and send it over raw HTTP. Explicit `upstream_stream_transport` overrides MUST still take precedence.

#### Scenario: large request payload routes via HTTP transport on auto
- **GIVEN** `upstream_stream_transport` is `"auto"` and the request payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large request payload bypasses the HTTP responses bridge
- **GIVEN** the HTTP responses bridge is enabled and the request payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Clean upstream close before any response event fails fast

When the HTTP Responses bridge observes an upstream WebSocket close with
`close_code = 1000` before any `response.*` event has been surfaced for the
pending request, the proxy MUST preserve its existing pre-visible replay
guards. If the request has already used exactly one eligible pre-visible
replay and the replacement upstream WebSocket also closes cleanly before any
response event, the proxy MAY perform exactly one additional replay. The
additional replay MUST be hard-capped at one per request, and the configured
maximum MUST NOT raise that cap.

The proxy MUST NOT replay after downstream-visible output, after a terminal
response event, or when continuity-sensitive request state makes replay unsafe.
Before the additional replay, the proxy MAY sleep for bounded configured
jitter. The proxy MUST emit a dedicated low-cardinality diagnostic event for
the additional replay.

When a downstream HTTP stream task initiates pre-response recovery while the
upstream reader is blocked on the superseded socket, the proxy MUST cancel and
await that reader before locally closing the socket. It MUST then start exactly
one reader for the replacement socket. A close caused by replacing the socket
MUST NOT be recorded as an upstream clean-close failure, MUST NOT increment the
retry circuit, and MUST NOT retire pending work moved to the replacement. The
cancelled reader's socket-generation finalizer MUST NOT leave the shared session
marked closed while the replacement socket is being selected or opened, so idle
pruning MUST NOT evict the handoff in progress.

The default pre-response idle-recovery window MUST leave bounded headroom
before the downstream client's request timeout. With the default ten-second
keepalive interval, the proxy MUST initiate eligible recovery after no more
than six silent intervals so replacement connection and first output can occur
before a 120-second client deadline.

The stuck pre-response watchdog MUST judge staleness using elapsed time since
the last upstream activity and the absence of a response identifier or
`response.created` latency, not admission flags alone. A request with a prior
continuity anchor MUST receive at most two retire-thresholds of grace before
being considered stale. When the watchdog skips a candidate, it MUST emit a
low-cardinality diagnostic containing the session-closed state, candidate
count, and pending-state verdicts.

#### Scenario: clean close before response.created is not retried

- **WHEN** the initial upstream HTTP responses bridge closes with `close_code = 1000` before any `response.*` event for the pending request
- **THEN** the proxy returns HTTP 502 with `error.code = "upstream_rejected_input"`
- **AND** does not transparently replay the pre-created request

#### Scenario: clean close before response output receives one bounded additional replay

- **GIVEN** an HTTP bridge request has no surfaced `response.*` events
- **AND** its first pre-visible replay has already been used
- **WHEN** the replacement upstream WebSocket closes with code `1000`
- **THEN** the proxy performs one additional pre-visible replay
- **AND** the request replay count increases by one
- **AND** the proxy emits a `retry_precreated_clean_close` diagnostic event

#### Scenario: repeated clean closes do not create an unbounded replay loop

- **GIVEN** the additional clean-close replay has already been used
- **WHEN** another upstream WebSocket closes cleanly before response output
- **THEN** the proxy does not replay the request again
- **AND** the existing terminal or circuit handling is used

#### Scenario: visible output still prevents clean-close replay

- **GIVEN** the pending request has surfaced any response event downstream
- **WHEN** the upstream WebSocket closes with code `1000`
- **THEN** the proxy does not replay the request

#### Scenario: clean-close retry jitter is bounded

- **GIVEN** clean-close retry jitter is configured
- **WHEN** the additional clean-close replay is scheduled
- **THEN** the delay is no greater than the configured jitter maximum
- **AND** the hard replay cap remains one regardless of the configured value

#### Scenario: downstream idle recovery transfers reader ownership

- **GIVEN** the upstream reader is blocked on the current bridge socket
- **AND** the downstream HTTP stream task initiates eligible pre-response recovery
- **WHEN** the bridge replaces the upstream socket
- **THEN** the old reader is cancelled and awaited before its socket is closed
- **AND** the shared session remains live while the replacement socket opens
- **AND** idle pruning retains the registered session while the handoff is in progress
- **AND** exactly one reader owns the replacement socket
- **AND** the local close does not open or increment the retry circuit
- **AND** pending work remains attached to the replacement session

#### Scenario: silent pre-response recovery precedes the client timeout

- **GIVEN** the upstream has produced no response event
- **AND** the default ten-second keepalive interval is active
- **WHEN** six silent intervals elapse
- **THEN** the proxy initiates eligible pre-response recovery
- **AND** at least sixty seconds remain before a 120-second client request timeout

#### Scenario: anchored stuck-gate grace is bounded

- **GIVEN** a pending HTTP bridge request has a prior continuity anchor
- **AND** no response identifier or `response.created` latency has been recorded
- **WHEN** less than two retire thresholds have elapsed since the gate began waiting
- **THEN** the watchdog does not classify the request as stale
- **WHEN** two retire thresholds elapse without upstream activity
- **THEN** the watchdog may classify the request as stale

#### Scenario: upstream activity resolves admission-flag ambiguity

- **GIVEN** a pending request has not acquired the response-created gate
- **AND** upstream activity has not produced a response identifier or `response.created`
- **WHEN** the staleness threshold elapses
- **THEN** the watchdog classifies the request as stale
- **AND** emits pending-state verdict inputs when it skips a watchdog pass

### Requirement: Durable retry-circuit state protects repeated hard-affinity failures

For a hard-affinity bridge key, the proxy MUST scope retry-circuit state by
affinity kind, affinity key, and API-key scope (using a stable anonymous scope
when no API key is present). The proxy MUST record only the documented
pre-response failure classes (`stream_incomplete`, `clean_close`, and
`stream_idle_timeout`).

A bridge retirement MUST record one of those failures only when the retiring
session still owns at least one pending request and no response event has been
observed for that request lifecycle. Retiring an idle upstream bridge with no
pending request MUST NOT advance the circuit or cause a later request to be
treated as a repeated failure. A pending request that has already emitted a
response event MUST remain excluded from this pre-response circuit.

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown.

The clean-close retry jitter maximum MUST be read from the
`http_responses_session_bridge_clean_close_retry_jitter_max_seconds` runtime
setting and MUST be bounded to the inclusive range 0–30 seconds.

The proxy MUST evict process-local circuit entries and their loaded/persisted
markers after one hour without use, independently of durable-row cleanup, so
one-shot hard-affinity keys cannot grow the worker's memory without bound.

Before every hard-affinity retry decision, the proxy MUST refresh the durable
row so a cooldown opened by another replica is observed even when this process
has already loaded the key. A durable lookup or persistence failure MUST NOT
crash the request; the proxy MUST continue using available local state and
record the failure for observability. Rows older than one hour MUST be treated
as expired and removed. A successful terminal response MUST clear the local
and durable circuit state.

#### Scenario: idle bridge retirement does not consume a circuit strike

- **GIVEN** a hard-affinity HTTP bridge has no pending requests
- **WHEN** its upstream WebSocket closes and the idle bridge is retired
- **THEN** the retry-circuit failure count for that key remains unchanged
- **AND** a later request is not placed in cooldown because of the idle close

#### Scenario: eventless pending retirement consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** the bridge retires because the upstream fails before acknowledging the request
- **THEN** the retry circuit records exactly one failure for that request lifecycle

#### Scenario: midstream retirement does not consume a pre-response strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with an observed response event
- **WHEN** the bridge retires before completion
- **THEN** the pre-response retry-circuit failure count remains unchanged

#### Scenario: the second hard-key failure opens a durable circuit

- **GIVEN** a hard-affinity key has one recorded pre-response failure
- **WHEN** a second eligible failure is recorded
- **THEN** the proxy opens the retry circuit
- **AND** persists at least two consecutive failures and a cooldown deadline
- **AND** subsequent pre-created replay is suppressed until that deadline

#### Scenario: retry decisions observe a cooldown opened by another replica

- **GIVEN** this replica previously looked up a hard-affinity key with no row
- **AND** another replica persists an open cooldown for that same key and API-key scope
- **WHEN** this replica evaluates the next pre-created retry
- **THEN** it refreshes durable state before deciding
- **AND** suppresses the retry for the persisted cooldown

#### Scenario: circuit state remains isolated by key and API-key scope

- **GIVEN** one hard-affinity key has an open circuit
- **WHEN** a different affinity key or API-key scope evaluates a retry
- **THEN** that request is not suppressed by the first key's circuit

#### Scenario: durable circuit lookup failure does not fail the request

- **GIVEN** durable retry-circuit lookup or persistence is unavailable
- **WHEN** the proxy evaluates or records a retry-circuit event
- **THEN** the request continues using any available local circuit state
- **AND** the failure is logged and exposed through retry-circuit observability

### Requirement: Long Codex websocket turns tolerate extended upstream silence
The default compact request budget MUST be at least 180 seconds, and the default upstream stream idle timeout MUST be at least 600 seconds, so long-running Codex turns can survive expensive compaction or tool execution without a local proxy watchdog ending the turn prematurely. Responses streams over both HTTP and WebSocket transports MUST use `http_responses_stream_request_budget_seconds` when it is configured; they MUST fall back to `proxy_request_budget_seconds` only when no stream-specific budget is available.

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

### Requirement: Responses upstream websocket liveness is bounded

The proxy MUST configure direct and routed upstream Responses WebSocket transports with finite ping/pong liveness detection derived from `proxy_downstream_websocket_idle_timeout_seconds`. When an established Responses WebSocket is terminated because its transport did not receive the required pong, the adapter MUST classify the failure as `upstream_websocket_liveness_timeout`. Direct WebSocket and HTTP bridge relay owners MUST treat that failure as account neutral, MUST NOT transparently replay a pending request whose delivery is ambiguous, MUST finalize its pending request ownership exactly once, and MUST retire the affected upstream socket so a later client retry opens a fresh connection. An HTTP bridge reader MUST suppress its own pending-deque settlement only when a concurrent submitter explicitly claimed liveness-settlement ownership under the session lifecycle lock; `session.closed` alone MUST NOT suppress settlement.

#### Scenario: Direct Responses websocket loses pong liveness

- **GIVEN** a direct upstream Responses WebSocket has been established
- **WHEN** the `websockets` keepalive watchdog terminates it after a pong timeout
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

### Requirement: Upstream websocket drops penalize affected accounts
When an upstream websocket closes while one or more streamed response requests
are pending and have not reached a terminal event, the proxy MUST record a
transient upstream error for the account before signaling failure for those
pending requests, except when the close carries a classified process-wide
network failure or upstream WebSocket liveness timeout, is a clean close
(`close_code = 1000`) before any `response.*` event, or carries the classified
per-socket `upstream_keepalive_timeout` transport error. Clean pre-response
closes, keepalive timeouts, process-wide network failures, and liveness
timeouts MUST remain account-neutral and use their classified error and bounded
retry or retry-circuit handling. For other closes, the proxy MUST surface
`stream_incomplete` to affected pending requests except when a direct Responses
WebSocket request has already successfully emitted a finite integer
`sequence_number`. For that sequenced direct-WebSocket case, the proxy MUST
record the request outcome as `stream_incomplete` without emitting a synthetic
terminal frame under the active response id, then MUST close the downstream
WebSocket with code 1011.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence, or the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure or upstream WebSocket liveness timeout
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: sequenced direct websocket closes before completion

- **GIVEN** a direct Responses WebSocket request has successfully emitted a finite integer `sequence_number`
- **WHEN** the upstream websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure or upstream WebSocket liveness timeout
- **THEN** the request is recorded as failed with `stream_incomplete`
- **AND** no synthetic terminal frame is emitted under the active response id
- **AND** the downstream WebSocket closes with code 1011
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: websocket liveness timeout remains account neutral

- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** its transport reports `upstream_websocket_liveness_timeout`
- **THEN** the pending request fails with that classified error code
- **AND** the account receives no failure-health signal
- **AND** the request is not transparently replayed

#### Scenario: clean pre-response close does not penalize the account

- **GIVEN** a hard-affinity HTTP bridge request is pending with no surfaced response event
- **WHEN** the upstream websocket closes cleanly before response output
- **THEN** the proxy records the clean-close retry-circuit outcome
- **AND** the selected account is not penalized

### Requirement: HTTP SSE stream idle timeouts remain account-neutral

When an HTTP SSE Responses stream's first upstream event is `response.failed` with `code=stream_idle_timeout`, the proxy MUST exclude that account from the remainder of the same request and MAY fail over to another account. It MUST NOT write account error-health (`record_error`, rate-limit, quota, or permanent failure) for that idle timeout. Request logs MUST still record `stream_idle_timeout` on the idle attempt.

#### Scenario: First-event stream idle timeout failovers without health penalty

- **GIVEN** an HTTP SSE Responses stream whose first upstream event is `response.failed` with `code=stream_idle_timeout`
- **AND** another healthy account is available
- **WHEN** the proxy retries the request
- **THEN** the idle account is excluded from the remainder of this request
- **AND** the idle account receives no error-health write
- **AND** the client receives the later account's successful stream
- **AND** the idle attempt's request log still uses `error_code=stream_idle_timeout`

### Requirement: Single HTTP bridge previous-response misses recover or fail closed
When an HTTP bridge session receives an anonymous upstream `previous_response_not_found` error for a single pending follow-up request, the service MUST treat the error as an internal continuity-loss signal. It MUST either recover through the existing previous-response rebind path or rewrite the error to a retryable continuity failure instead of forwarding the raw upstream invalid-request error.

#### Scenario: single pending HTTP bridge follow-up loses previous-response continuity
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` bridge session has exactly one pending request with `previous_response_id`
- **AND** upstream emits `previous_response_not_found` without a `response.id`
- **THEN** the service attempts the existing previous-response recovery path
- **AND** if recovery is unavailable, it emits a retryable continuity failure for that request
- **AND** the downstream error code is not `previous_response_not_found`

### Requirement: WebSocket full-resend previous-response misses retry without stale anchor
When a direct WebSocket `response.create` request includes both `previous_response_id` and a self-contained full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error. A payload that only carries incremental tool outputs for tool calls that are not also present in the same request is not self-contained and MUST NOT be replayed as a fresh turn without `previous_response_id`.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full resend
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives the recovered response events, not the raw `previous_response_not_found` error

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` items without their matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without `previous_response_id`
- **AND** the downstream client receives a retryable continuity failure rather than a fabricated fresh turn

### Requirement: Parameterless invalid previous-response errors use continuity recovery

When an upstream Responses WebSocket rejects an anchored request with `type = "invalid_request_error"`, no `code` or `param`, and the normalized message ``Invalid `previous_response_id``` with or without one trailing period, the service MUST classify the frame as a previous-response continuity miss. It MUST apply the same replay, masking, ownership, and account-health rules as the canonical `previous_response_not_found` error and MUST NOT relay the raw invalid-request frame downstream. A different named parameter or any other trailing punctuation MUST NOT match this error shape.

#### Scenario: Codex-native delta continuation receives the canonical recovery signal

- **GIVEN** a Codex-native `/backend-api/codex/responses` request carries `previous_response_id` and delta-only tool output that cannot be replayed safely without its anchor
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error before `response.created`
- **THEN** the downstream client receives a sanitized error with `code = "previous_response_not_found"`
- **AND** the raw upstream envelope and previous response id are not exposed

#### Scenario: Self-contained full resend is replayed without the rejected anchor

- **GIVEN** an anchored direct WebSocket request retains a self-contained full-resend body that is safe to replay without `previous_response_id`
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error before `response.created`
- **THEN** the service reconnects and replays the retained body without `previous_response_id`
- **AND** the raw upstream error is not sent downstream

#### Scenario: Public WebSocket retains generic continuity masking

- **GIVEN** a public `/v1/responses` WebSocket request carries `previous_response_id` but cannot be replayed safely without its anchor
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error
- **THEN** the downstream client receives the existing sanitized `stream_incomplete` continuity failure
- **AND** neither `previous_response_not_found` nor the raw upstream envelope is exposed

#### Scenario: Unrelated invalid requests retain their original classification

- **WHEN** upstream returns `invalid_request_error` with a different message or names a parameter other than `previous_response_id`
- **THEN** the service MUST NOT classify that error as a previous-response continuity miss

### Requirement: Public Responses errors mask previous-response misses
Public Responses endpoints MUST NOT return an OpenAI-shaped `previous_response_not_found` error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable `stream_incomplete` continuity failure and remove the missing response id from the public payload.

#### Scenario: API layer receives an upstream previous-response miss
- **WHEN** a public `/responses`, `/v1/responses`, `/responses/compact`, or `/v1/responses/compact` handler receives an error with `code=previous_response_not_found`
- **OR** it receives `code=invalid_request_error` with `param=previous_response_id` and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is `stream_incomplete`
- **AND** the missing `previous_response_id` is not exposed in the response body

### Requirement: Public /v1 responses SSE stream emits only OpenAI Responses contract events
When serving streaming `POST /v1/responses`, the service MUST emit only event types defined by the OpenAI Responses SSE contract (the `response.*` and `error` families) on the public stream. The service MUST drop any vendor-internal event types — specifically, any event whose `type` begins with `codex.` (for example `codex.rate_limits`) — before they reach the public stream. The `/backend-api/codex/*` routes are NOT subject to this requirement and MUST continue forwarding these events unchanged.

#### Scenario: Codex-internal rate-limit event is dropped before response.created
- **WHEN** the upstream Codex backend emits `codex.rate_limits` before `response.created` for a streaming `/v1/responses` request
- **THEN** the public stream MUST NOT contain the `codex.rate_limits` event
- **AND** the first event the public stream emits MUST be `response.created`

#### Scenario: Codex-internal events on the Codex CLI route are preserved
- **WHEN** the upstream emits `codex.rate_limits` for a `POST /backend-api/codex/responses` request
- **THEN** the response stream forwards the `codex.rate_limits` event to the Codex CLI client unchanged

### Requirement: Streamed /v1 responses terminal output is backfilled from item events
When serving streaming `POST /v1/responses`, if the upstream's terminal `response.completed` or `response.incomplete` event carries `output` as missing or as an empty list, the service MUST reconstruct `output` from the `response.output_item.done` events emitted earlier in the same stream before yielding the terminal SSE event. The reconstructed `output` MUST preserve the `output_index` ordering and the raw item payloads. When the terminal `response.completed` / `response.incomplete` already carries a non-empty `output`, the service MUST forward it unchanged.

#### Scenario: Terminal response.completed with empty output is backfilled from streamed items
- **GIVEN** the upstream emits `response.output_item.done` events with valid message or function-call items
- **WHEN** the upstream's terminal `response.completed` event carries `output: []`
- **THEN** the public stream's terminal `response.completed` event MUST carry the reconstructed `output` array, populated from the streamed `output_item.done` items in `output_index` order
- **AND** an OpenAI Python SDK consumer calling `stream.get_final_response().output` MUST receive the same populated list

#### Scenario: Terminal response.completed already carries output
- **WHEN** the upstream's terminal `response.completed` event already includes a non-empty `output` array
- **THEN** the public stream's terminal event MUST carry that `output` array unchanged

### Requirement: Public /v1 responses SSE stream starts with response.created
When serving streaming `POST /v1/responses`, the first OpenAI-contract event the public stream emits MUST be `response.created`. When the upstream's first standard `response.*` event is not `response.created` (for example when the Codex backend jumps directly to `response.failed` on upstream rejection mid-stream), the service MUST synthesize a `response.created` SSE event from the source event's `response` envelope and emit it before forwarding the source event, so that consumers using the OpenAI Python SDK's `responses.stream(...)` parser do not raise `RuntimeError`.

#### Scenario: Upstream error stream that skips response.created is repaired
- **WHEN** the upstream's first standard event is `response.failed` (no preceding `response.created`)
- **THEN** the public stream MUST emit a synthesized `response.created` event derived from the failed event's `response` envelope before forwarding the `response.failed` event
- **AND** an OpenAI Python SDK consumer iterating the stream MUST NOT raise `RuntimeError` from the parser's initial-response check

#### Scenario: Normal stream is not double-emitted
- **WHEN** the upstream's first standard event is already `response.created`
- **THEN** the public stream MUST emit exactly one `response.created` event (no synthesized duplicate)

### Requirement: Upstream overload envelopes are classified as retryable transient failures

When `classify_upstream_failure` observes an upstream error envelope whose `code` is `overloaded_error` or `server_is_overloaded`, the system MUST treat it as `retryable_transient` regardless of the accompanying HTTP status. Streamed Responses API traffic can deliver the overload envelope on a connection that has already returned HTTP 200, so a 5xx-only heuristic is insufficient to drive account fail-over and bounded retry.

#### Scenario: `overloaded_error` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the failover layer is eligible to retry the request or fail over to another account instead of returning a non-retryable error to the client

#### Scenario: `overloaded_error` with a 5xx status remains retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` is 500, 502, 503, or 504
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the result is the same as the no-status path, so the 5xx fallback heuristic is not the only signal driving the decision

#### Scenario: `server_is_overloaded` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="server_is_overloaded"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the streaming retry layer is eligible to retry the request before surfacing the terminal overload event

#### Scenario: HTTP bridge retries a pre-created overload event

- **GIVEN** the HTTP responses session bridge is enabled
- **WHEN** the first upstream `response.failed` or `error` event has `code="overloaded_error"` or `code="server_is_overloaded"`
- **THEN** the bridge MUST retry the pre-created request before forwarding that terminal event
- **AND** the bridge MUST preserve its existing no-replay behavior after downstream-visible output or for other fail-fast error codes

### Requirement: Strict function tool parameter schemas are pre-validated

The service MUST pre-validate the JSON schema attached to a function tool when that tool sets `strict: true`, before opening any upstream connection. The validation rules mirror OpenAI's Structured Outputs strict-mode policy (https://platform.openai.com/docs/guides/structured-outputs) and the existing `enforce_strict_text_format` policy for `text.format.json_schema`:

- Every `object` schema node MUST set `additionalProperties: false`.
- Every property under `properties` MUST appear in `required`.
- Every schema node MUST carry a `type` key (no empty `{}` schemas).
- The same rules apply recursively to nested object / array / combinator (`anyOf` / `oneOf` / `allOf`) schemas.

When any of those rules is violated, the service MUST reject the request with `HTTP 400 invalid_request_error` carrying:

- `error.code = "invalid_function_parameters"`
- `error.message = "Invalid schema for function '<name>': In context=<path>, <reason>."`
- `error.param = "tools[<index>].parameters"` for native Responses-API requests; `error.param = "tools[<index>].function.parameters"` for chat-completions requests routed through the coercion pipeline.

This brings strict function tool schema handling into parity with `text.format.json_schema`. Without it, an invalid strict tool schema reaches the upstream Codex backend, which closes the WebSocket with `close_code=1000` and surfaces as a generic `502 server_error / upstream_rejected_input`. Real OpenAI returns `400 invalid_function_parameters` for the identical payload. A 5xx on a deterministically-broken request also triggers retry / failover loops in well-behaved clients.

#### Scenario: Strict tool missing `additionalProperties` is rejected with 400

- **WHEN** a client sends `tools: [{"type": "function", "name": "f", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}, "strict": true}]`
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"`, `error.message` matching `/Invalid schema for function 'f': In context=\(\), 'additionalProperties' is required to be supplied and to be false\./`, and `error.param = "tools[0].parameters"`

#### Scenario: Strict tool with `additionalProperties: true` is rejected

- **WHEN** a client sends a function tool with `strict: true` and `parameters.additionalProperties = true`
- **THEN** the proxy returns `HTTP 400 invalid_function_parameters` with the same `'additionalProperties' is required to be supplied and to be false` message

#### Scenario: Strict tool with property missing from `required` is rejected

- **WHEN** a client sends a function tool with `strict: true`, `additionalProperties: false`, but `required` omits one of the listed `properties`
- **THEN** the proxy returns `HTTP 400 invalid_function_parameters` with the `'required' is required to be supplied and to be an array including every key in properties` message

#### Scenario: Compliant strict tool is accepted

- **WHEN** a client sends a function tool with `strict: true`, `additionalProperties: false`, and every property listed in `required`
- **THEN** the proxy forwards the request to the upstream unchanged and the response is `200`

#### Scenario: `strict: false` or omitted strict skips pre-validation

- **WHEN** a client sends a function tool with `strict: false` or without a `strict` key, and the schema would have violated strict mode (e.g. missing `additionalProperties`)
- **THEN** the proxy does not run the strict pre-validation and forwards the request unchanged, matching pre-fix behavior for non-strict tools

### Requirement: Same-response side-effect tool-call replays are suppressed

When the proxy receives multiple downstream `response.output_item.done` events for the same response that describe the same side-effecting local tool operation, the proxy SHALL forward only the first event to the client.

The proxy SHALL treat `exec_command`, `write_stdin`, `multi_tool_use.parallel`, and `apply_patch_call` events as side-effecting. For these tools, a changed `call_id` alone MUST NOT make a same-response replay distinct.

When a `multi_tool_use.parallel` event contains duplicate nested side-effect operations, the proxy SHALL remove the duplicate nested operations before forwarding the event. Duplicate nested `exec_command` operations MUST ignore volatile output/wait fields such as `yield_time_ms` and `max_output_tokens`. Duplicate nested `write_stdin` operations MUST be scoped by `session_id` and `chars`. Duplicate nested `wait_agent` operations MUST be scoped by the target set.

Read-only function calls and matching operations under different response ids MUST continue to pass through.

#### Scenario: side-effect call replay uses a new call id

- **WHEN** a streamed response emits two `exec_command` output items with the same response id and arguments but different call ids
- **THEN** the proxy forwards the first event
- **AND** suppresses the second event

#### Scenario: read-only call ids stay distinct

- **WHEN** a streamed response emits two read-only function calls with the same arguments and different call ids
- **THEN** the proxy forwards both events

#### Scenario: later response ids stay distinct

- **WHEN** two responses emit the same side-effecting operation under different response ids
- **THEN** the proxy forwards both events

#### Scenario: parallel batch contains duplicate shell operations

- **WHEN** a `multi_tool_use.parallel` event contains two nested `functions.exec_command` operations with the same command and only different wait/output fields
- **THEN** the proxy forwards one nested operation inside the parallel batch
- **AND** does not forward the duplicate nested operation to the client

### Requirement: Continuity-dependent Responses follow-ups fail closed with retryable errors
When a Responses follow-up depends on previously established continuity state, the service MUST return a retryable continuity error if that continuity cannot be reconstructed safely. The service MUST NOT expose raw `previous_response_not_found` for bridge-local metadata loss or similar internal continuity gaps. When forwarding a turn-state-anchored follow-up to its bridge owner fails with `bridge_owner_unreachable` and a fresh durable lookup shows the owner no longer holds an active lease (released, expired, or the row is missing or CLOSED), the service MUST recover the follow-up locally through durable takeover instead of returning the retryable error. The fresh durable lookup MUST use the same resolution semantics as request routing, including the latest-turn-state fallback, so a row originally resolved without a registered alias remains takeover-eligible. When the durable lease is still actively held by another instance — including DRAINING rows whose lease has not been released or expired — the service MUST keep failing closed with the retryable error.

#### Scenario: HTTP bridge loses local continuity metadata for a follow-up request
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` follow-up request depends on `previous_response_id` or a hard continuity turn-state
- **AND** the bridge cannot reconstruct the matching live continuity state from local or durable metadata
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: in-flight bridge follower loses continuity while waiting on the same canonical session
- **WHEN** a follow-up request waits on an in-flight HTTP bridge session for the same hard continuity key
- **AND** the bridge still cannot reconstruct safe continuity state once the leader finishes
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: multiplexed follow-ups fail closed only for the matching continuity anchor
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests with different `previous_response_id` anchors
- **AND** continuity loss is detected for exactly one of those anchors
- **THEN** the service applies the retryable fail-closed continuity error only to the matching follow-up request
- **AND** it does not expose raw `previous_response_not_found`
- **AND** unrelated pending requests continue on their own response lifecycle

#### Scenario: multiplexed follow-ups sharing one anchor fail closed together without leaking raw continuity errors
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests that share the same `previous_response_id` anchor
- **AND** upstream emits an anonymous continuity loss event such as `previous_response_not_found` for that shared anchor
- **THEN** the service rewrites each affected follow-up into a retryable continuity error
- **AND** no affected follow-up exposes raw `previous_response_not_found`
- **AND** the run remains usable for subsequent requests after the rewritten failures

#### Scenario: single pre-created follow-up still fails closed when continuity loss omits explicit response id in message
- **WHEN** a websocket follow-up request is pending with `previous_response_id` and has not received a stable upstream `response.id` yet
- **AND** upstream emits `previous_response_not_found` with `param=previous_response_id`
- **AND** the upstream error message omits the literal previous response identifier
- **THEN** the service still maps that continuity loss to the pending follow-up
- **AND** it rewrites the downstream terminal event to a retryable continuity error
- **AND** it does not surface raw `previous_response_not_found` to the client

#### Scenario: turn-state follow-up recovers locally after the owner released its lease
- **WHEN** a turn-state-anchored follow-up without `previous_response_id` is forwarded to its bridge owner during the post-shutdown ring grace window
- **AND** the forward fails with `bridge_owner_unreachable`
- **AND** a fresh durable lookup using the request-routing resolution semantics (registered alias or latest-turn-state fallback) shows the lease is released or expired
- **THEN** the service retries the follow-up locally through durable takeover instead of returning the retryable 503
- **AND** the takeover retry carries the fresh durable lookup as its continuity anchor even when the turn-state alias registration was lost
- **AND** a fresh durable lookup showing a live lease held by another instance — even for a DRAINING row — still fails closed with the retryable `bridge_owner_unreachable` error

### Requirement: Live DRAINING durable leases reject foreign claims

When a durable HTTP-bridge session is `DRAINING` and another instance still holds an unexpired lease, a foreign `claim_live_session` MUST leave the current owner and lease unchanged even when `allow_takeover` is true. Local session create MUST use the same live-owner predicate as turn-state takeover and MUST NOT treat `DRAINING` alone, or a forced recovery after a missing ring endpoint, as permission to steal a live `DRAINING` lease. The locked claim row, not a stale pre-claim lookup, MUST be the source of the `DRAINING` decision. Expired, released, or `CLOSED` rows MUST remain takeover-eligible.

#### Scenario: Foreign claim refuses a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **WHEN** instance B claims the same key with `allow_takeover` false
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`
- **AND** A's lease expiry is unchanged

#### Scenario: Forced claim still refuses after an ACTIVE lookup becomes live DRAINING

- **GIVEN** instance A owns a durable session whose lookup snapshot is still `ACTIVE`
- **AND** instance B would force takeover because A's endpoint is missing
- **AND** A marks the row `DRAINING` with a live lease before B's claim lock
- **WHEN** B claims the same key with `allow_takeover` true
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`

#### Scenario: Missing owner endpoint does not force-steal a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **AND** the ring cannot resolve A's endpoint
- **WHEN** instance B creates a local HTTP-bridge session for the same key
- **THEN** the durable claim is issued with `allow_takeover` false
- **AND** A's owner and lease remain unchanged

#### Scenario: Expired DRAINING row remains takeover-eligible

- **GIVEN** a `DRAINING` durable session whose lease is expired or whose owner is released
- **WHEN** another instance claims the same key
- **THEN** that instance becomes the owner
- **AND** the row becomes `ACTIVE`

### Requirement: Hard continuity owner lookup fails closed

When a request depends on hard continuity ownership, the service MUST fail
closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT
continue with account selection that bypasses hard owner enforcement. A direct
WebSocket continuation already attached to its required open owner socket MUST
NOT be failed solely because a new per-turn selection attempt temporarily
excludes that owner.

#### Scenario: websocket previous-response owner lookup errors

- **WHEN** a websocket or HTTP fallback follow-up includes
  `previous_response_id`
- **AND** owner lookup errors prevent determining the required owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not continue on an unpinned account

#### Scenario: bridge owner or ring lookup errors for hard continuity keys

- **WHEN** an HTTP bridge request uses a hard continuity key such as turn-state,
  explicit session affinity, or `previous_response_id`
- **AND** owner or ring lookup errors prevent proving the correct bridge owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not create or recover a local bridge session on the current
  replica

#### Scenario: required owner differs from the open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to an owner different from the
  currently open upstream account
- **THEN** the service retires the current upstream socket
- **AND** reconnects the unchanged anchored request to the required owner
- **AND** it does not forward any `x-codex-turn-state` associated with the
  retired account, whether supplied by the client or learned upstream

#### Scenario: required owner matches the healthy open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to the currently open owner
- **THEN** the service sends it on that socket without a new selector-based
  eligibility check

### Requirement: Request logs persist requested, actual, and billable service tiers separately
For Responses proxy traffic, the system MUST persist the operator-requested tier, the upstream-reported actual tier when available, and the effective billable tier used for pricing as separate request-log fields.

The legacy `fast` alias MUST be normalized to the canonical upstream value
`priority` before forwarding and before it is stored as the requested tier.
The upstream-reported `response.service_tier`, when present, remains the
authoritative actual tier even when it differs from the requested tier.

#### Scenario: Upstream reports a downgraded actual tier
- **WHEN** a client sends a Responses request with `service_tier: "priority"`
- **AND** the upstream response later reports `service_tier: "default"`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = "default"`
- **AND** the persisted request log entry records billable `service_tier = "default"`

#### Scenario: Fast alias is logged as a priority request
- **WHEN** a client sends a Responses request with `service_tier: "fast"`
- **AND** the upstream response later reports `service_tier: "default"`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = "default"`
- **AND** the persisted request log entry records billable `service_tier = "default"`

#### Scenario: Upstream omits the actual tier
- **WHEN** a client sends a Responses request with `service_tier: "priority"`
- **AND** the upstream response omits `service_tier`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = null`
- **AND** the persisted request log entry records billable `service_tier = "priority"`

### Requirement: API key service tier enforcement applies to upstream Responses requests

When an API key carries an enforced service tier, the proxy MUST override any
incoming Responses request service tier with that enforced value before route
selection. The omit-equivalent client values `auto` and `default` MUST count as
an omitted tier when tracking whether the enforced value supplied the request's
tier. The legacy alias `fast` MUST be treated as `priority`.

For a subscription-account route, when an authoritative account catalog says
the selected model never advertises the enforced tier, the proxy MUST remove
that tier from the effective request before account selection and upstream
forwarding. The resulting effective tier MUST survive internal owner
forwarding unchanged. This fallback MUST NOT remove an explicit non-default
client tier, MUST NOT alter a request routed through an external model source,
and MUST NOT apply when the account catalog has no authoritative answer for the
model.

#### Scenario: Enforced service tier overrides the request payload

- **GIVEN** the selected account model advertises the `priority` service tier
- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request asks for `service_tier: "default"`
- **THEN** the forwarded upstream payload uses `service_tier: "priority"`

#### Scenario: Omit-equivalent request permits account-catalog fallback

- **GIVEN** an account model authoritatively advertises no `priority` service tier
- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request omits `service_tier` or supplies `auto` or `default`
- **THEN** the account-routed upstream payload omits `service_tier`
- **AND** an internal owner forward preserves that effective omission

#### Scenario: Explicit non-default tier is not downgraded

- **GIVEN** an account model authoritatively advertises no `priority` service tier
- **WHEN** a client explicitly requests `service_tier: "priority"` or the equivalent `fast` alias
- **THEN** API-key enforcement does not make the tier eligible for account-catalog fallback

#### Scenario: Fast alias is applied as priority

- **WHEN** an API key is configured with `enforcedServiceTier: "fast"`
- **THEN** the forwarded upstream payload uses the canonical value `priority`

### Requirement: Cursor GPT-5 model aliases normalize to canonical slugs

For Responses proxy traffic, the service MUST recognize Cursor-style GPT-5 model aliases formed by appending known suffix tokens
(`minimal`, `low`, `medium`, `high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to supported GPT-5 family slugs, including the GPT-5.6
personality slugs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`. The alias
resolver MUST match longer qualified canonical slugs before shorter family prefixes so aliases such as `gpt-5.4-mini-high` and `gpt-5.3-codex-fast` normalize
to the intended model. Unknown suffix tokens MUST leave the requested model unchanged; `ultra` and `max` are not suffix tokens (they are not effort levels
every GPT-5-family base supports — `gpt-5.6-luna` advertises no `ultra`), so
labels such as `gpt-5.6-sol-ultra` pass through unchanged.

#### Scenario: Qualified mini model alias normalizes reasoning

- **WHEN** a client sends a Responses request with `model: "gpt-5.4-mini-high"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.4-mini"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`

#### Scenario: Qualified codex model alias normalizes service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.3-codex-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.3-codex"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 personality alias normalizes reasoning and service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-extra-high-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.6-sol"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 ultra-suffixed label is not rewritten

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-ultra"`
- **THEN** the forwarded upstream request keeps `model: "gpt-5.6-sol-ultra"` unchanged

### Requirement: OpenAI-compatible Responses payload sanitation removes provider-specific thinking aliases

The shared OpenAI-compatible Responses sanitation path MUST normalize third-party thinking aliases into the canonical `reasoning` object before upstream forwarding. Unknown provider-specific thinking controls MUST NOT be passed through unchanged to the upstream ChatGPT backend.

#### Scenario: Shared payload sanitation maps enable_thinking

- **WHEN** an internal Responses payload contains `enable_thinking: true`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Explicit reasoning wins over provider aliases

- **WHEN** an internal Responses payload contains both `reasoning: {"effort":"high"}` and `thinking: {"type":"enabled"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "high"`
- **AND** the forwarded upstream payload does not include `thinking`

### Requirement: Public Responses streams expose renderable final text
For OpenAI-style streaming `/v1/responses` and `/backend-api/codex/responses`, the service MUST expose renderable `response.output_text.delta` events for assistant message text when upstream provides final text only in output item or terminal response output payloads. The service MUST NOT duplicate text deltas for an output item that already emitted a text delta.

#### Scenario: final output item text is exposed as a text delta
- **WHEN** upstream emits a `response.output_item.done` event with assistant message text and no prior text delta for that output item
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the final item event

#### Scenario: terminal response output text is exposed as a text delta
- **WHEN** upstream emits only a terminal `response.completed` event with assistant message text in `response.output`
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the terminal event

#### Scenario: existing text deltas are preserved without duplication
- **WHEN** upstream already emits a `response.output_text.delta` for an output item
- **THEN** the service forwards the stream without synthesizing another text delta for that same output item

### Requirement: Tool call events and output items are preserved
If the upstream model emits tool call deltas or output items, the service MUST forward those events in streaming mode and MUST include tool call items in the final response output for non-streaming mode.

#### Scenario: Tool call emitted
- **WHEN** the upstream emits a tool call delta event
- **THEN** the service forwards the delta event and includes the finalized tool call in the completed response output

#### Scenario: Chat Completions tool arguments avoid snapshot duplication
- **WHEN** `/v1/chat/completions` maps Responses tool-call events that include incremental deltas and later finalized snapshots for the same tool call
- **THEN** the final `tool_calls[].function.arguments` value is exactly one valid JSON string for that tool call
- **AND** the adapter MUST NOT append full snapshot payloads on top of already-collected incremental argument deltas

#### Scenario: Parallel tool calls route arguments by output_index
- **WHEN** `/v1/chat/completions` maps Responses events for two or more parallel function calls
- **THEN** the adapter MUST route each event to its `tool_calls[]` slot using the event's `output_index` as the primary routing key
- **AND** the adapter MUST preserve a stable mapping from `output_index` to the same slot across `output_item.added`, `output_item.done`, `response.function_call_arguments.delta`, and `response.function_call_arguments.done` events for that call
- **AND** parallel tool calls MUST NOT collapse to index `0` when their argument-only events identify the owning call only via `item_id`

#### Scenario: Parallel tool calls also resolve through item_id aliases
- **WHEN** an `output_item.added` or `output_item.done` event exposes both `item.id` (e.g. `"fc_..."`) and `item.call_id` (e.g. `"call_..."`)
- **THEN** the adapter MUST register `item.id` as an alias to the same `tool_calls[]` slot as the `call_id`
- **AND** subsequent argument-only events that carry only `item_id` MUST resolve to that aliased slot, even if their `output_index` has not yet been observed

#### Scenario: Internal item_id never leaks into the public call identifier
- **WHEN** the adapter exposes a tool call to the client as `tool_calls[].id` or `tool_calls[].call_id`
- **THEN** the value MUST be the upstream `call_...` identifier and MUST NOT be substituted with the internal `fc_...` item id used solely for routing

### Requirement: Responses routing prefers budget-safe accounts
When serving Responses routes, the service MUST prefer eligible accounts that are still below the configured budget threshold over eligible accounts already above that threshold. If no below-threshold candidate exists, the service MAY fall back to the pressured candidates.

#### Scenario: Fresh Responses request avoids a near-exhausted account
- **WHEN** `/backend-api/codex/responses`, `/backend-api/codex/responses/compact`, `/v1/responses`, or `/v1/responses/compact` selects among multiple eligible active accounts
- **AND** one candidate is above the configured budget threshold
- **AND** another candidate remains below that threshold
- **THEN** the below-threshold candidate is chosen first

### Requirement: Upstream Responses event size budget
The service SHALL allow upstream Responses SSE events and upstream websocket message frames up to 16 MiB by default before treating them as oversized.

#### Scenario: built-in tool output exceeds the old 2 MiB limit
- **WHEN** upstream Responses traffic includes a single SSE event or websocket message frame larger than 2 MiB but not larger than 16 MiB
- **THEN** the proxy continues processing the event instead of closing the upstream websocket locally with `1009 message too big`

### Requirement: Upstream Responses transport strategy
For streaming Codex/Responses proxy requests, the system MUST let operators choose the upstream transport strategy through dashboard settings. The resolved strategy MAY be `auto`, `http`, or `websocket`, and `default` MUST defer to the server configuration default.

#### Scenario: Dashboard forces websocket upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"websocket"`
- **THEN** streaming Responses requests use the upstream websocket transport

#### Scenario: Dashboard forces HTTP upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"http"`
- **THEN** streaming Responses requests use the upstream HTTP/SSE transport

#### Scenario: Auto transport falls back when websocket upgrades are rejected
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** auto selection chose the websocket transport
- **AND** the upstream rejects the websocket upgrade with HTTP `426`
- **THEN** the proxy retries the request over the upstream HTTP/SSE transport

#### Scenario: Session affinity alone does not trigger websocket upstream transport
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** a request includes a `session_id`
- **AND** it does not include an allowlisted native Codex `originator` or explicit Codex websocket feature headers
- **THEN** the auto strategy MUST keep using the existing model-preference transport selection rules

#### Scenario: Auto transport honors websocket-preferred bootstrap models before registry warmup
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the model registry has not loaded a snapshot yet
- **AND** the request targets a locally bootstrapped websocket-preferred model family such as `gpt-5.4` or `gpt-5.4-*`
- **AND** the request does not include the built-in `image_generation` tool
- **THEN** the proxy chooses the upstream websocket transport

#### Scenario: Auto transport prefers HTTP for image-generation tool requests
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the request includes a built-in `image_generation` tool
- **THEN** the proxy chooses the upstream HTTP/SSE transport even if the model would otherwise prefer websocket

#### Scenario: Legacy settings preserve the pre-feature default
- **WHEN** transport selection runs against a legacy settings object that does not expose the newer upstream transport fields
- **THEN** the proxy MUST preserve the pre-feature HTTP transport default for model-preference auto-selection unless an explicit legacy websocket mode or native Codex websocket signal opts in

### Requirement: Responses-compatible tool payload handling
The service SHALL accept built-in Responses tool definitions on `/backend-api/codex/responses` and `/v1/responses` without locally rejecting them. The service MAY normalize documented aliases, but upstream model/tool compatibility validation MUST remain the upstream contract.

#### Scenario: full Responses request includes built-in tools
- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with built-in Responses tools such as `image_generation`, `computer_use`, `computer_use_preview`, `file_search`, or `code_interpreter`
- **THEN** the proxy forwards those tool objects upstream instead of returning a local `invalid_request_error`

### Requirement: Compact requests drop tool-only fields
The service SHALL remove `tools` and `tool_choice` from compact request payloads, and set `parallel_tool_calls` to `false`, before calling the upstream compact endpoint.

#### Scenario: compact request reuses a full Responses payload shape

- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `tools`, `tool_choice`, or `parallel_tool_calls`
- **THEN** the proxy drops `tools` and `tool_choice` before the upstream compact request
- **AND** the proxy sends `parallel_tool_calls` as `false`
- **AND** the compact request continues without a local or upstream `invalid_request_error` caused by `param="tools"`

### Requirement: Responses requests accept input_file content items with a file_id

The system SHALL accept `input_file` content items that reference an upload by `file_id` in `/backend-api/codex/responses` and `/v1/responses` request payloads (both list-form and string-form `input`). These items MUST be forwarded to upstream verbatim. The same MUST apply to `/responses/compact` request bodies. The proxy MUST NOT raise `input_file.file_id is not supported` for these items.

#### Scenario: input_file with file_id is accepted in a /responses request

- **WHEN** a client posts a `/v1/responses` request whose `input` contains a `{"type": "input_file", "file_id": "file_abc"}` content item
- **THEN** the request validates and the upstream payload includes that content item unchanged

#### Scenario: input_file with file_id is accepted in a compact request

- **WHEN** a client posts a `/responses/compact` request whose `input` contains an `input_file` item with a `file_id`
- **THEN** the request validates and is forwarded to upstream verbatim

### Requirement: Responses requests with input_file.file_id route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files` when a durable, unexpired pin for that `file_id` exists. The pin MUST be visible to every replica that shares the application database. A live file pin is hard ownership evidence: it MUST override prompt-cache or bare process-session locality and MUST agree with independently resolved turn-state, previous-response, bridge, or other hard ownership.

When multiple `file_id`s are referenced, all live pins MUST resolve to the same account. If at least one ID has a live pin and another ID has no live pin, the request MUST fail with `file_owner_unavailable`; if live pins resolve to different accounts, it MUST fail with `continuity_owner_conflict`. If none of the referenced IDs has a live pin, the proxy MUST preserve compatibility with files registered directly upstream or before durable ownership was observed by forwarding the opaque IDs verbatim under ordinary unpinned routing.

A live durable pin MUST NOT be reassigned to another account. Repeating the claim for the same account MUST be idempotent and MAY renew its expiry; an expired identifier MAY be claimed by a later upload.

Every hard file-owner decision MUST read the shared database and MUST NOT rely on a process-local owner cache. Authenticated inter-replica forwarding metadata MAY corroborate the freshly resolved durable owner but MUST NOT replace the receiver's database read. A missing or conflicting receiver-side durable owner MUST fail closed before account selection or upstream invocation. Pin expiry, reclaim, and cleanup MUST use database-authoritative statement time rather than a replica's application clock.

For a streaming Responses request whose durable file-owner lookup runs in the stream service, any API-key usage reservation acquired before that lookup MUST have exactly one cleanup owner if resolution fails or the request is cancelled. Within one replica, the API layer MUST own cleanup until the direct stream service enters its settlement-guarded `try/finally` or the local HTTP-bridge service successfully submits the request and installs its request-state finalizer. The service finalizer MUST own cleanup after that explicit boundary so those layers cannot both release the reservation. Merely completing the durable lookup MUST NOT transfer cleanup before a service finalizer is active, and an initial SSE heartbeat MUST NOT transfer ownership to the client.

When an authenticated HTTP-bridge origin forwards that reservation to another replica, the receiver MUST delay its successful HTTP 200 response until its service finalizer is active. That 200 response MUST be the cleanup-handoff acknowledgement that transfers ownership from the origin to the receiver. The origin MUST distinguish a request that has not been dispatched, a dispatch with no observed response status, a successful HTTP 200 acknowledgement, and a definitive non-200 rejection. Before dispatch or after a definitive non-200, receiver-side owner-revalidation failure or cancellation MUST propagate with cleanup remaining at the origin. After dispatch when no response status can be observed, the origin MUST NOT actively release or replay the reservation because the receiver may already own settlement; receiver settlement or bounded stale-reservation cleanup MUST resolve that ambiguity. After the acknowledgement, the receiver service finalizer MUST remain authoritative even if no upstream event has arrived. If a bounded startup probe hands pending preflight work to the response body and the body closes first, the active owner MUST cancel and await that work before scheduling one cancellation-safe release attempt. If that persistence write fails, the same cleanup owner MUST schedule one follow-up release attempt instead of abandoning the reservation. An SSE heartbeat or another frame MUST NOT transfer cleanup ownership. Compact service settlement MUST likewise suppress a second API-layer release after its single settlement attempt. Once a forwarded compact service has made that settlement attempt, including when both the primary finalize and the fallback release fail, a later receiver-side output validation failure or a `usage_settlement_failed` error MUST preserve HTTP 200 as the cleanup-handoff acknowledgement and surface a terminal `response.failed` event with the stable error code; it MUST NOT become a non-200 rejection that permits origin release or replay. A client disconnect after the initial SSE heartbeat MUST close the service stream even when the startup probe already completed. A cleanup-store failure MUST NOT replace a stable owner-resolution error. A cleanup-store failure MUST NOT mask the original stable owner error or cancellation. Owner-lookup failure or cancellation MUST NOT trigger account failover or another upstream attempt.

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a` on one replica
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}` on another replica
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: file_id pin overrides prompt-cache locality

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST route to `account_a` and MUST NOT send the account-scoped file to the prompt-cache account

#### Scenario: opaque file_id without a live pin remains compatible

- **GIVEN** a request references a `file_id` registered directly upstream or before the system durably observed its upload
- **AND** no referenced file has a live durable pin
- **WHEN** the request is routed
- **THEN** the proxy MUST forward the `file_id` verbatim under ordinary unpinned routing
- **AND** it MUST NOT reject the request solely because owner metadata is absent

#### Scenario: file finalize resolves ownership across replicas

- **GIVEN** one replica registered `file_xyz` through `account_a`
- **WHEN** another replica handles `POST /backend-api/files/file_xyz/uploaded`
- **THEN** the proxy MUST finalize the file through `account_a`
- **AND** it MUST NOT fall back to a different eligible account

#### Scenario: concurrent live ownership claims do not overwrite

- **GIVEN** `file_xyz` has a live durable pin to `account_a`
- **WHEN** another replica attempts to pin `file_xyz` to `account_b`
- **THEN** the claim MUST fail with `continuity_owner_conflict`
- **AND** subsequent routing MUST still resolve `file_xyz` to `account_a`

#### Scenario: a replica observes an expired pin reclaimed by another replica

- **GIVEN** a replica previously resolved `file_xyz` to `account_a`
- **AND** the durable pin expires and another replica claims `file_xyz` for `account_b`
- **WHEN** the first replica resolves `file_xyz` again
- **THEN** it MUST read the durable owner and return `account_b`
- **AND** it MUST NOT return `account_a` from process-local state

#### Scenario: durable owner lookup failure fails closed

- **GIVEN** a request references a file whose owner decision requires the shared database
- **WHEN** the durable owner lookup fails
- **THEN** the request MUST fail before selecting or invoking an unpinned fallback account

#### Scenario: cancellation during owner lookup releases admission state

- **GIVEN** a request has acquired an API-key usage reservation before durable file-owner resolution completes
- **WHEN** the request is cancelled while the owner lookup is pending
- **THEN** exactly one cleanup owner MUST attempt to release or settle the reservation
- **AND** no account selection, upstream invocation, retry, or failover may occur

#### Scenario: delayed owner failure after stream handoff releases admission state

- **GIVEN** the streaming startup probe expires while durable file-owner resolution is still pending
- **WHEN** the lookup later fails or the response body is closed
- **THEN** the origin API MUST cancel and await any still-pending lookup
- **AND** the origin API MUST make exactly one release attempt
- **AND** a lookup failure MUST be represented by the stable `file_owner_unavailable` error

#### Scenario: failed reservation release is retried

- **GIVEN** a startup or disconnect cleanup owns an API-key reservation
- **WHEN** the first persistence release fails
- **THEN** the cleanup owner MUST schedule one follow-up release attempt
- **AND** it MUST NOT leave the reservation reserved with no later cleanup path

#### Scenario: forwarded owner metadata is revalidated against durable ownership

- **GIVEN** a replica receives authenticated forwarding metadata that identifies `account_a` as a referenced file's owner
- **WHEN** the receiver's fresh durable lookup has no live owner or identifies a different owner
- **THEN** the receiver MUST fail closed
- **AND** it MUST NOT route using the forwarded value alone
- **AND** it MUST propagate the preflight failure to the origin without releasing the origin reservation
- **AND** the originating request path MUST remain the sole cleanup owner because no successful handoff acknowledgement was sent

#### Scenario: forwarded stream acknowledges cleanup ownership before HTTP 200

- **GIVEN** the origin forwards a file-pinned streaming request and its API-key reservation to the authenticated owner replica
- **WHEN** the receiver completes durable owner revalidation and installs its service settlement finalizer
- **THEN** the receiver MAY return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** the origin MUST stop releasing the reservation after receiving that acknowledgement
- **AND** cancellation before the first upstream event MUST invoke only the receiver's service finalizer

#### Scenario: ambiguous owner dispatch defers active origin cleanup

- **GIVEN** the origin has begun dispatching a signed forwarded request carrying its reservation
- **WHEN** the transport fails before the origin can observe an HTTP status
- **THEN** the origin MUST NOT actively release or replay the reservation
- **AND** receiver settlement or stale-reservation cleanup MUST remain the only recovery paths

#### Scenario: definitive owner rejection retains origin cleanup

- **GIVEN** the origin dispatches a signed forwarded request carrying its reservation
- **WHEN** the receiver returns a non-200 response without acknowledging cleanup handoff
- **THEN** the origin MUST make exactly one cancellation-safe release attempt
- **AND** the receiver MUST NOT settle the origin reservation

#### Scenario: owner non-200 remains a rejection after body-read failure

- **GIVEN** the origin has observed a non-200 owner-forward status
- **WHEN** reading the rejection body then fails
- **THEN** the origin MUST treat the outcome as a definitive rejection
- **AND** it MUST NOT reclassify the dispatch as ambiguous

#### Scenario: compact service settlement is not released twice

- **GIVEN** terminal or direct compaction receives an API-key usage reservation
- **WHEN** the compact service makes its single settlement or release attempt
- **THEN** the API layer MUST NOT issue another release for that reservation
- **AND** a pre-service failure MUST still leave exactly one release attempt at the API layer

#### Scenario: malformed compact output after settlement preserves handoff

- **GIVEN** a forwarded terminal compact request whose receiver service has made its single settlement attempt
- **WHEN** the settled response lacks a valid compaction output item
- **THEN** the receiver MUST return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: compact settlement failure after fallback preserves handoff

- **GIVEN** a forwarded terminal compact request whose receiver service has made its single settlement attempt
- **WHEN** usage settlement fails after a successful fallback release
- **THEN** the receiver MUST return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event with code `usage_settlement_failed`
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: compact settlement attempt preserves handoff when both writes fail

- **GIVEN** a forwarded terminal compact request whose receiver service attempts settlement
- **WHEN** both reservation finalization and the fallback release fail
- **THEN** the receiver MUST still return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event with code `usage_settlement_failed`
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: completed startup probe still closes the service stream

- **GIVEN** the streaming startup probe already obtained the first service event
- **WHEN** the client disconnects after the initial SSE heartbeat
- **THEN** the origin MUST close the service stream
- **AND** reservation cleanup MUST still run if ownership has not transferred

### Requirement: Soft HTTP-bridge 1011 reconnect keeps a live file-pin owner

A still-unsubmitted HTTP-bridge reconnect MUST keep a live `input_file.file_id`
pin as a required owner after a soft session closes with `1011`.
When an HTTP-bridge session is soft (prompt-cache or request locality) and
upstream closed it with `1011`, a still-unsubmitted request that carries a
live `input_file.file_id` pin MUST keep that pin account as a required
reconnect owner. The proxy MUST NOT exclude that account solely because the
close code was `1011`, and MUST NOT fall back to another account while the
pin is live. If the required pin account is already excluded or cannot be
reconnected, the proxy MUST fail closed with the existing required-owner
unavailable error. A soft `1011` reconnect that has no live file pin and no
other required owner MAY still skip the closed account.

#### Scenario: Soft 1011 reconnect keeps the file-pin account required

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **WHEN** the proxy reconnects that session
- **THEN** account selection MUST treat `account_a` as the required owner
- **AND** it MUST NOT add `account_a` to the excluded-account set solely because of `1011`
- **AND** it MUST NOT enable preferred-account fallback to another account

#### Scenario: Soft 1011 reconnect without a file pin may skip the closed account

- **GIVEN** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the still-unsubmitted request has no live file pin and no other required owner
- **WHEN** the proxy reconnects that session
- **THEN** account selection MAY exclude `account_a` and choose another eligible account

#### Scenario: Soft 1011 file-pin reconnect fails closed when the required owner cannot be selected

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **AND** account selection cannot return `account_a`
- **WHEN** the proxy reconnects that session
- **THEN** the proxy MUST fail closed with the existing required-owner unavailable error
- **AND** it MUST NOT replace that envelope with a generic selection failure

#### Scenario: Soft 1011 file-pin reconnect fails closed when the required owner cannot be connected

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **AND** account selection returns `account_a`
- **AND** opening a replacement upstream for `account_a` fails
- **WHEN** the proxy reconnects that session on submit
- **THEN** the client-visible error MUST be the existing required-owner unavailable error
- **AND** it MUST NOT be replaced with a generic `upstream_unavailable` envelope

### Requirement: Codex backend session_id preserves account affinity
When a backend Codex Responses or compact request includes a non-empty accepted session header, the service MUST use that value as the routing affinity key for upstream account selection unless the client supplied a non-empty `x-codex-turn-state` header. If the request lacks a client-supplied `prompt_cache_key`, the service MUST derive and attach a stable `prompt_cache_key` before upstream forwarding so account affinity and upstream prompt-cache routing can coexist. Accepted session headers are `session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, and `thread-id`, in that priority order.

A turn state synthesized by the proxy for the current downstream WebSocket handshake MUST NOT override a client-supplied session header or prompt-cache key for routing or WebSocket continuity selection. The proxy MUST seed WebSocket continuity storage under that synthesized turn state so a later client echo can reuse the completed-turn owner. The proxy MUST continue to forward that synthesized turn state upstream. A turn state sent by the client, including one that the proxy generated and the client later echoed, remains a client-supplied turn-state affinity key.

When a WebSocket handshake has neither a client-supplied turn state nor an accepted session header, the proxy MUST store its generated turn state as the WebSocket continuity key. A later connection that echoes that accepted value MUST recover the same continuity state.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing
- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `prompt_cache_key`
- **THEN** the routing decision still uses durable `codex_session` affinity for account selection
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

#### Scenario: backend WebSocket reconnect retains session affinity despite a generated turn state
- **WHEN** two backend Codex Responses WebSocket connections include the same accepted session header and omit `x-codex-turn-state`
- **AND** the proxy generates a distinct turn state for each handshake
- **THEN** both account selections use the session header as the durable `codex_session` affinity key
- **AND** each generated turn state is still forwarded to the upstream

#### Scenario: echoed generated turn state remains a client continuation key
- **WHEN** a client reconnects with a non-empty `x-codex-turn-state` value it received from an earlier proxy handshake
- **THEN** that turn state remains the routing and WebSocket continuity key ahead of a broader accepted session header
- **AND** full-resend continuity for that echoed turn state can reuse the earlier completed response anchor

#### Scenario: generated turn state seeds continuity without a session header
- **WHEN** a backend Codex Responses WebSocket handshake omits both an accepted session header and `x-codex-turn-state`
- **AND** the proxy generates and returns a turn state for that handshake
- **THEN** the proxy stores its WebSocket continuity state under that generated value
- **AND WHEN** a later connection sends that value in `x-codex-turn-state`
- **THEN** it recovers the stored continuity state

### Requirement: Proxy-generated prompt cache key derivation is operator-toggleable
The service MUST provide a runtime flag that disables only proxy-generated prompt-cache-key derivation. When disabled, the service MUST continue forwarding any client-supplied `prompt_cache_key` unchanged and MUST NOT synthesize a new one.

#### Scenario: Derivation disabled preserves client-supplied key
- **WHEN** the derivation flag is disabled and a client sends `prompt_cache_key`
- **THEN** the service forwards that key unchanged
- **AND** it does not generate a replacement key

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the service MUST preserve upstream Responses websocket session continuity on a stable per-session bridge key instead of opening a brand new upstream session for every eligible request. The bridge key MUST use an explicit session/conversation header when present; otherwise it MUST use normalized `prompt_cache_key`, and when the client omits `prompt_cache_key` the service MUST derive a stable key from the same cache-affinity inputs already used for OpenAI prompt-cache routing. While bridged, the service MUST preserve the external HTTP/SSE contract, MUST continue request logging with `transport = "http"`, and MUST keep requests from different bridge keys isolated from one another.

#### Scenario: bridge forwards hard continuity keys to the owner replica
- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a bridge key derived from `x-codex-turn-state` or an explicit session header
- **AND** that request lands on a non-owner instance
- **THEN** the service MUST forward the request internally to the owner replica
- **AND** it MUST NOT return a topology-bearing `bridge_instance_mismatch` error to the client for that owner mismatch alone

#### Scenario: gateway-style prompt-cache bridge requests tolerate wrong-replica arrival
- **WHEN** a request uses a bridge key derived only from `prompt_cache_key` or a derived prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service MAY create or reuse a local bridge session on that instance
- **AND** it MUST treat the owner mismatch as a locality miss instead of a continuity failure

#### Scenario: forwarded bridge requests fail closed when owner forwarding loops
- **WHEN** a forwarded hard-continuity bridge request reaches another non-owner replica
- **THEN** the service MUST fail the request with a generic 5xx bridge-forward error
- **AND** it MUST NOT attempt another owner handoff

#### Scenario: local restart orphan is recovered by the replacement instance
- **WHEN** a single local bridge instance is replaced while durable hard-continuity ownership still references the old instance id
- **AND** the old owner has no distinct active forwarding endpoint from the current replacement instance
- **THEN** the replacement instance MUST treat the row as restart-orphaned and may claim durable ownership locally
- **AND** same-account takeover MUST preserve the latest persisted response anchor until a replacement response id is recorded
- **AND** normal client retries MUST NOT be stranded waiting for the old instance lease to expire

When request aliases resolve to different durable rows for the same account,
an explicitly requested previous-response alias MUST select its row even if
that row has since advanced to a newer response id. Without an explicitly
resolved previous-response alias, recovery MUST select the freshest row that
contains a persisted response anchor rather than using alias enumeration order.

#### Scenario: requested durable response alias survives same-account row divergence

- **GIVEN** turn-state and previous-response aliases resolve to different durable rows for the same account
- **AND** the request names the previous-response alias whose row has since advanced to a newer response id
- **WHEN** the service resolves durable continuity
- **THEN** it selects the row resolved by the requested previous-response alias
- **AND** it preserves that row's latest persisted response anchor

### Requirement: Responses account selection accounts for in-flight pressure

For Responses API requests, usage-based routing MUST include immediate in-process account pressure in addition to persisted usage. Account selection MUST account for in-flight response-create work, active streams, leased token/cost estimates, recent selection pressure, account health, and configured account-local caps. Selection and lease acquisition MUST be atomic with respect to other in-process selections, and the critical section MUST NOT perform database calls, network calls, sleeps, or other blocking I/O.

#### Scenario: Concurrent burst spreads before upstream usage refreshes

- **GIVEN** multiple eligible accounts have similar persisted usage
- **WHEN** many `/v1/responses` requests arrive concurrently before upstream usage refreshes
- **THEN** selected accounts are distributed according to immediate in-flight pressure and caps
- **AND** one account does not receive all requests solely because persisted usage was stale

#### Scenario: File-pinned bridge request does not reroute under local pressure

- **GIVEN** an HTTP bridge `/v1/responses` request references an `input_file.file_id` pinned to an upstream account
- **AND** that owner account or bridge session rejects admission with local pressure before output starts
- **WHEN** the proxy handles the admission failure
- **THEN** it returns the owner account overload instead of soft-rerouting the payload to another account
- **AND** the file-scoped request is not replayed to an account that does not own the file

#### Scenario: Runtime lock excludes blocking I/O

- **WHEN** account selection holds the balancer runtime lock
- **THEN** the implementation performs only in-memory scoring and lease mutation
- **AND** database, network, sleep, or bridge queue waits happen outside that lock

### Requirement: Account leases release on all terminal paths

Every account-local lease acquired for a Responses request MUST be idempotently released or settled on success, upstream error, local startup error, bridge submit failure, startup probe conversion, non-streaming collect completion, failover, downstream disconnect, cancellation, timeout, and retry. A bounded stale-lease watchdog MUST reclaim leases that survive unexpected task cancellation or exceptions, and stale reclamation MUST emit warning/metric evidence. Leases MUST NOT be persisted to the database.

#### Scenario: Lease releases after downstream disconnect

- **WHEN** a streaming `/v1/responses` client disconnects before a terminal upstream event
- **THEN** the account stream lease is released exactly once
- **AND** later routing pressure no longer includes that stream

#### Scenario: WebSocket local account cap releases API-key reservation

- **GIVEN** a WebSocket `response.create` has reserved API-key usage
- **AND** account-local response-create lease acquisition fails with `account_response_create_cap`
- **WHEN** the proxy emits the local terminal failure
- **THEN** the API-key usage reservation is released
- **AND** the pending request is removed from websocket local state

#### Scenario: Stale watchdog recovers orphaned lease

- **WHEN** a request task exits unexpectedly after acquiring an account lease
- **AND** the lease exceeds the configured TTL
- **THEN** the watchdog releases the stale lease
- **AND** emits a low-cardinality warning/metric

#### Scenario: Active stream lease is not reclaimed before valid stream budget

- **GIVEN** a stream lease is older than the base lease TTL
- **AND** the configured Responses stream or HTTP bridge request budget has not elapsed
- **WHEN** account lease stale reclamation runs
- **THEN** the stream lease still counts against account-local stream pressure
- **AND** the proxy does not admit extra streams over the account stream cap by age alone

### Requirement: Public Responses streaming is proxy-timeout friendly

Streaming `/v1/responses` responses MUST include anti-buffering/cache headers suitable for SSE through common front-door proxies and MUST emit an early flushable SSE comment or event before long upstream startup waits can appear idle. Periodic SSE keepalive behavior MUST continue while waiting for upstream events. These heartbeat comments MUST NOT violate the public Responses event contract: OpenAI-contract events still begin with `response.created` when event parsing ignores comments.

#### Scenario: Streaming response includes anti-buffering headers

- **WHEN** a client starts streaming `POST /v1/responses`
- **THEN** the response headers include SSE content type and anti-buffering/cache directives
- **AND** the headers are present before upstream response completion

#### Scenario: Early heartbeat precedes long upstream silence

- **WHEN** upstream startup takes longer than the heartbeat interval
- **THEN** the client receives a flushable SSE heartbeat before a front-door origin idle timeout would trigger
- **AND** the first OpenAI-contract event remains `response.created` when upstream accepts the request

### Requirement: Codex WebSocket top-level previous-response errors are masked
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy MUST treat upstream `type: "error"` frames with top-level error fields as upstream error envelopes if the frame does not contain a nested `error` object. If those fields describe a `previous_response_not_found` continuity miss, the proxy MUST use the existing continuity fail-closed behavior and MUST NOT forward raw `previous_response_not_found` or the missing response id to the downstream Codex client.

#### Scenario: ChatGPT backend emits top-level previous-response miss on Codex websocket
- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** the ChatGPT backend emits `{"type":"error","code":"previous_response_not_found","param":"previous_response_id",...}` without a nested `error` object
- **THEN** the downstream event is a retryable continuity failure such as `stream_incomplete`
- **AND** the downstream payload does not contain `previous_response_not_found`
- **AND** the downstream payload does not expose the missing previous response id

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

### Requirement: Codex WebSocket pre-created turns receive application heartbeats
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy SHALL emit a parseable Codex vendor heartbeat while a `response.create` request is pending but upstream has not yet emitted `response.created`. The heartbeat MUST be an application text frame so Codex clients reset stream-idle watchdogs that do not observe WebSocket protocol ping/pong frames. Once upstream assigns a response id, the proxy MUST continue using the existing `response.in_progress` heartbeat shape for that response id.

#### Scenario: Codex websocket upstream is silent before response.created
- **GIVEN** a Codex-native WebSocket `/backend-api/codex/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy emits a `codex.keepalive` text event downstream
- **AND** the request remains pending for the upstream `response.created` or terminal event

#### Scenario: OpenAI-style v1 websocket does not receive Codex vendor heartbeat
- **GIVEN** an OpenAI-style WebSocket `/v1/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy MUST NOT emit a `codex.keepalive` vendor event downstream

### Requirement: WebSocket terminal auth failures recover before visible output

When a Codex or OpenAI-compatible Responses WebSocket request receives an upstream terminal `response.failed` or `error` before downstream-visible output with `error.code = "invalid_api_key"` or `error.type = "authentication_error"`, the proxy MUST treat the failure as account-local auth state instead of immediately surfacing the terminal event. The proxy MUST preserve the existing no-replay rule after downstream-visible output or for non-replayable continuation requests.

#### Scenario: Session-ended WebSocket auth failure uses another account

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal auth failure whose message says the session ended or asks the user to log in again
- **WHEN** another eligible account can complete the request
- **THEN** the downstream WebSocket response succeeds from the other account
- **AND** the selected account is marked re-authentication-required and excluded from that replay

#### Scenario: Generic WebSocket auth failure refreshes once before failover

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal `invalid_api_key` failure
- **WHEN** the forced-refresh replay on the selected account also returns a pre-visible terminal `invalid_api_key` failure
- **THEN** the proxy excludes the selected account and tries another eligible account
- **AND** the downstream WebSocket response succeeds from the other account when it completes

#### Scenario: WebSocket auth failure after visible output is not replayed

- **GIVEN** a WebSocket response has emitted downstream-visible output
- **WHEN** upstream later returns a terminal `invalid_api_key` or `authentication_error`
- **THEN** the proxy MUST surface the terminal error without replaying the request on another account

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key` or `401 token_invalidated`
response for the selected account, the proxy MUST attempt one forced token
refresh and retry the compact request on that same account. If the refreshed
retry also returns `401`, the proxy MUST classify and record the account
failure, exclude that account from the current compact request, and try another
eligible account when one is available. The proxy MUST NOT surface the repeated
account-local `401` to the compact client before exhausting eligible accounts.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Refreshed compact token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 token_invalidated` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is marked `reauth_required`
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the low-level compact transport does not mark it as a generic same-contract transport retry

### Requirement: Pre-visible proxy auth failures fail over after forced refresh

The proxy MUST treat repeated account-local authentication failures as
per-request account failures before any downstream-visible output is emitted.
When a proxy request on a non-compact surface retries with a refreshed token and
the refreshed retry still returns upstream `401 invalid_api_key` or
`401 token_invalidated`, the proxy MUST classify and record the selected account
failure, exclude that account from the current request, and try another eligible
account when one is available. The proxy MUST preserve the existing no-replay
rule after downstream-visible stream or websocket output has been emitted.

#### Scenario: Pre-visible streaming auth failure uses another account

- **GIVEN** at least two accounts are eligible for a streaming responses request
- **AND** the selected account returns `401 invalid_api_key` before downstream-visible output
- **WHEN** another eligible account can complete the request
- **THEN** the downstream stream succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Pre-visible token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a pre-visible proxy request
- **AND** the selected account returns `401 token_invalidated` before and after a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is marked `reauth_required`

#### Scenario: Non-stream proxy auth failure uses another account

- **GIVEN** at least two accounts are eligible for a thread-goal, Codex control,
  transcription, or file create/finalize request
- **AND** the selected account returns `401 invalid_api_key` before and after a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Websocket connect auth failure uses another account

- **GIVEN** at least two accounts are eligible for an upstream websocket connect
- **AND** the selected account returns `401 invalid_api_key` after a forced refresh retry
- **WHEN** another eligible account can open the upstream websocket
- **THEN** the websocket connect path excludes the invalidated account and tries another account

#### Scenario: HTTP bridge handshake auth failure uses another account

- **GIVEN** at least two accounts are eligible for HTTP bridge session creation or reconnect
- **AND** the selected account returns `401 invalid_api_key` after a forced refresh retry
- **WHEN** another eligible account can open the upstream websocket handshake
- **THEN** the HTTP bridge path excludes the invalidated account and tries another account

### Requirement: Codex WebSocket wrapped errors follow official client shape

When serving `/backend-api/codex/responses` or bridge-backed Responses WebSocket traffic, the service MUST classify upstream `type: "error"` frames using the same wrapped-error shape that the official Codex client accepts: a non-2xx `status` or `status_code` field indicates an upstream HTTP-style error, and the error detail MAY appear either in a nested `error` object or in top-level fields such as `code`, `message`, `param`, and `error_type`.

Top-level error normalization MUST NOT treat the event discriminator `type: "error"` as the upstream error type. If the frame provides `error_type`, the service MUST use that value as the error type for classification/rewrites. Existing continuity protection remains authoritative: frames describing `previous_response_not_found` MUST be rewritten or recovered through the established `stream_incomplete` continuity path instead of exposing the raw upstream code or missing response id.

#### Scenario: status_code alias is classified as upstream error status

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status_code":400,...}`
- **THEN** the service treats the HTTP-style error status as `400`
- **AND** applies the same error classification path as for `status: 400`

#### Scenario: top-level error_type is used for classification

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status":400,"error_type":"invalid_request_error","code":"previous_response_not_found",...}`
- **THEN** the normalized error detail has `type = "invalid_request_error"`
- **AND** the event discriminator `type = "error"` is not used as the upstream error type

#### Scenario: top-level previous-response miss remains masked

- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable continuity failure such as `stream_incomplete`
- **AND** the downstream payload does not contain `previous_response_not_found`
- **AND** the downstream payload does not expose the missing previous response id

### Requirement: Backend Codex Responses preserve advertised image_generation tools

The service MUST accept HTTP and websocket `/backend-api/codex/responses`
request-create payloads that include top-level `tools` entries with
`type: "image_generation"`. During shared Responses validation and upstream
forwarding, the service MUST preserve those top-level `image_generation` tool
entries so Codex clients can expose and use the built-in image-generation
surface. The service MUST also preserve all other tool entries and the existing
built-in tool forwarding policy for public `/v1/*` routes.

#### Scenario: Backend Codex HTTP request preserves advertised image_generation tool

- **WHEN** a client sends `POST /backend-api/codex/responses` with
  `tools=[{"type":"image_generation"},{"type":"function","name":"x"}]`
- **THEN** the request is accepted instead of failing with
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool
- **AND** the remaining `function` tool is preserved

#### Scenario: Backend Codex websocket create preserves advertised image_generation tool

- **WHEN** a websocket `response.create` payload for
  `/backend-api/codex/responses` includes a top-level
  `{"type":"image_generation"}` tool entry
- **THEN** the backend Codex websocket request is accepted
- **AND** the forwarded upstream `response.create` payload preserves that
  `image_generation` tool entry

#### Scenario: Public v1 Responses built-in forwarding policy remains unchanged

- **WHEN** a client sends `/v1/responses` with
  `tools=[{"type":"image_generation"}]`
- **THEN** the service does not locally reject the built-in tool as an
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool

### Requirement: HTTP bridge startup waits fail with terminal local overload

When the HTTP responses bridge cannot start upstream work because its local bridge startup waits do not make progress within the configured proxy admission wait timeout, the service MUST surface a terminal local-overload error instead of leaving `/v1/responses`, `/backend-api/codex/responses`, or compact responses streams on keepalives only.

#### Scenario: HTTP bridge startup wait stalls before first upstream event

- **WHEN** a streaming Responses request enters the HTTP responses bridge
- **AND** bridge startup is blocked by local bridge admission state before any upstream `response.*` event can be emitted
- **AND** the wait exceeds the configured proxy admission wait timeout
- **THEN** the request fails with a terminal error
- **AND** the error payload identifies local proxy overload with `error.code = "proxy_overloaded"`

### Requirement: Accept duplicated /v1/ prefix under /backend-api/codex
The service MUST treat any inbound request whose path begins with `/backend-api/codex/v1/` followed by a non-empty rest as a transparent alias for the same path with the `/v1` segment removed. Some OpenAI-compatible clients append `/v1/` to whatever the operator configured as the base URL, producing paths like `/backend-api/codex/v1/models` or `/backend-api/codex/v1/responses`. The aliasing MUST be applied before routing so the canonical handler runs unchanged. The aliasing MUST NOT trigger for `/backend-api/codex/v1` or `/backend-api/codex` with no further path. The top-level OpenAI-style `/v1/<rest>` routes are unaffected.

#### Scenario: Misbehaving client requests duplicated prefix
- **WHEN** a client requests `GET /backend-api/codex/v1/models`
- **THEN** the response is identical to `GET /backend-api/codex/models`

#### Scenario: Canonical paths are unchanged
- **WHEN** a client requests `GET /backend-api/codex/models` or `GET /v1/models`
- **THEN** the request is routed to its existing handler without modification

### Requirement: Backend Responses endpoint accepts OpenAI-compatible request shapes
The `/backend-api/codex/responses` HTTP endpoint SHALL accept the OpenAI-compatible Responses request shape used by `/v1/responses`, including a plain string `input` and omitted or explicit `null` `instructions`. The endpoint MUST normalize that request into the internal Responses request model before forwarding upstream, MUST continue returning `text/event-stream` SSE Responses events, and MUST preserve Codex-specific session/cache affinity behavior for the backend route.

#### Scenario: OpenAI SDK streams through backend Responses path
- **WHEN** an OpenAI-compatible client sends `POST /backend-api/codex/responses` with `stream=true`, a model, and a plain string `input`
- **THEN** the proxy accepts the request without requiring `instructions`
- **AND** the response is a `text/event-stream` stream containing Responses events such as `response.output_text.delta` and `response.completed`

#### Scenario: Codex-private stream metadata is hidden from OpenAI SDK clients
- **WHEN** upstream emits a Codex-private stream event such as `codex.rate_limits` before `response.created`
- **THEN** the HTTP Responses stream omits the private event from the downstream SSE body
- **AND** OpenAI SDK clients can consume the stream without failing their Responses event ordering checks

#### Scenario: Strict function tool schemas are validated before streaming
- **WHEN** an OpenAI-compatible client sends `POST /backend-api/codex/responses` with a strict function tool schema that violates the supported JSON Schema subset
- **THEN** the proxy rejects the request with a deterministic 400 `invalid_function_parameters` error before opening the stream

#### Scenario: Codex-native backend Responses shape is preserved
- **WHEN** a Codex client sends `POST /backend-api/codex/responses` with `instructions`, array-shaped `input`, and Codex affinity headers
- **THEN** the proxy preserves the normalized request content and continues applying backend Codex session affinity

### Requirement: Codex WebSocket stale-anchor failures remain recoverable by a full-context retry
When serving or consuming the Codex-native `/backend-api/codex/responses` WebSocket route, upstream `previous_response_id` MUST be treated as an ephemeral optimization rather than durable conversation state. A stale-anchor continuity failure during a long-wait tool-output continuation MUST NOT hard-end the user turn before one full-context retry without `previous_response_id` has been attempted.

#### Scenario: Long-running terminal wait invalidates the upstream previous response anchor
- **GIVEN** a Codex-native WebSocket session has completed a response with id `resp_old`
- **AND** the client later sends a `response.create` frame with `previous_response_id: "resp_old"` and tool-output or other delta input after a long idle period
- **WHEN** the upstream rejects `resp_old` with a stale-anchor error such as `previous_response_not_found`
- **THEN** the failure is classified as stale-anchor continuity loss
- **AND** the client-side recovery path retries once using full conversation history without `previous_response_id` before surfacing a turn-ending error
- **AND** the downstream/user-visible error path does not expose raw `previous_response_not_found` or the missing upstream response id

#### Scenario: codex-lb sanitizes stale-anchor errors for client classification
- **WHEN** upstream emits a direct WebSocket stale-anchor error
- **THEN** codex-lb MUST NOT forward raw `previous_response_not_found`
- **AND** codex-lb MUST NOT expose the missing upstream response id downstream
- **AND** codex-lb MUST preserve a stable sanitized classifier that lets a compatible Codex client distinguish stale-anchor continuity loss from quota, policy, auth, and generic invalid-request failures

#### Scenario: Non-stale-anchor failures do not trigger full-context retry
- **WHEN** the upstream failure is quota, policy, auth, context-window, or another non-continuity error
- **THEN** the client MUST NOT convert it into a stale-anchor full-context retry
- **AND** codex-lb MUST preserve the original error class as much as safely possible

### Requirement: Codex WebSocket continuity source of truth is centralized
The behavior for Codex-native WebSocket previous-response continuity MUST be specified in this OpenSpec change rather than route-local or branch-local ad hoc patches. Future changes to this behavior MUST update the OpenSpec requirements before modifying code.

#### Scenario: Previous-response fix changes behavior
- **WHEN** a patch changes routing, replay, masking, retry, or failure behavior for Codex-native WebSocket `previous_response_id`
- **THEN** the patch includes an OpenSpec delta or updates the active continuity source of truth
- **AND** direct `/backend-api/codex/responses` WebSocket tests or Codex client WebSocket tests cover the changed behavior

### Requirement: Direct WebSocket previous-response misses never leak raw upstream errors
When a direct Responses WebSocket request depends on `previous_response_id`, the service MUST NOT send a raw upstream `previous_response_not_found` payload to the downstream client. This applies to `/v1/responses` and `/backend-api/codex/responses` WebSocket clients.

#### Scenario: Codex Desktop continue receives upstream previous-response miss before response.created
- **WHEN** a direct WebSocket `response.create` request includes `previous_response_id`
- **AND** upstream emits a top-level `type=error` payload with `code=previous_response_not_found` or `param=previous_response_id`
- **AND** no stable upstream `response.id` has been assigned yet
- **THEN** the downstream client receives either a transparent replay result or a retryable terminal event
- **AND** the downstream payload does not include `previous_response_not_found`
- **AND** the downstream payload does not include the missing previous response id

#### Scenario: Codex Desktop continue has only request-log owner metadata
- **WHEN** a prior direct WebSocket turn completed and was persisted only in `request_logs`
- **AND** a later direct WebSocket follow-up references that completed response id
- **THEN** owner lookup uses request-log metadata or fails closed with a retryable error
- **AND** it does not continue on an unpinned account
- **AND** it does not expose raw `previous_response_not_found`

### Requirement: Failed precreated HTTP bridge replay retires stale sessions

When an HTTP bridge request is still pending before upstream `response.completed` and the upstream websocket closes or times out before the pending request can be completed, the service MUST fail the pending request terminally and retire the affected bridge session if precreated replay does not reconnect and resend successfully.

#### Scenario: Precreated replay fails after upstream disconnect

- **WHEN** an HTTP bridge request is pending before `response.completed`
- **AND** the upstream websocket closes before the request completes
- **AND** precreated replay fails to reconnect and resend the request
- **THEN** the pending request is removed from the bridge queue
- **AND** the per-session response-create gate is released
- **AND** the bridge session is closed and removed from local reuse
- **AND** the terminal error preserves the original failure code such as `stream_incomplete` or `upstream_request_timeout`

#### Scenario: Terminal logging failure does not preserve stale bridge ownership

- **WHEN** a failed pending HTTP bridge request is being logged as terminal
- **AND** request-log writing fails
- **THEN** the service still removes the stale bridge session from local reuse
- **AND** the service releases any durable bridge ownership for that stale session

#### Scenario: Concurrent waiter cannot submit on retired stale bridge

- **WHEN** an HTTP bridge request is waiting on a session response-create gate
- **AND** the upstream reader retires that same bridge session after a failed precreated replay
- **THEN** the waiting request or prewarm is rejected before it is appended to pending requests or sent upstream
- **AND** the retired bridge session remains closed and removed from local reuse
- **AND** the post-admission ownership check, pending enqueue, and upstream send are mutually exclusive with stale-session retirement

#### Scenario: Unregistered stale bridge reference cannot submit after admission

- **WHEN** an HTTP bridge request or prewarm holds a stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected after response-create gate admission and before it is appended or sent upstream
- **AND** response-create gate and admission state acquired by the rejected request is released

#### Scenario: Unregistered closed bridge reference cannot reconnect

- **WHEN** an HTTP bridge request holds a closed stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected before attempting to reconnect the stale bridge upstream

#### Scenario: Reader crash closes bridge before releasing pending gate

- **WHEN** an HTTP bridge upstream reader crashes while a pending request owns the response-create gate
- **AND** another request or prewarm is waiting on that same gate
- **THEN** the crashed bridge session is marked closed before the pending request gate is released
- **AND** the waiting request or prewarm cannot submit on the crashed bridge
- **AND** the crashed bridge session is removed from local reuse and its upstream resources are closed

#### Scenario: Prewarm cleanup does not consume visible queue slots

- **WHEN** a prewarm request is rejected or interrupted after response-create gate admission
- **AND** a visible HTTP bridge request is still counted in the session queue
- **THEN** prewarm cleanup releases its response-create gate and admission state
- **AND** the visible request queue count is preserved

### Requirement: Pre-dispatch Responses requests recover from local network transitions

When a Responses request encounters a classified local DNS or host-route failure and the transport proves that request dispatch did not occur, the proxy MUST retry on the same account with bounded backoff until the attempt succeeds or the existing request budget expires. A classified token-refresh network failure MUST receive the same bounded same-account recovery only when typed transport provenance proves the refresh POST was not dispatched. Recovery MUST NOT move account-owned continuation or file state to another account. Recovery client rotation, client construction, cleanup, and sleep MUST remain inside the original monotonic deadline, and existing keepalive behavior MUST remain active while an HTTP/SSE client waits. Post-connect send or receive failures, response/body-read failures, and serialized terminal response events with uncertain upstream delivery MUST retain the account-neutral network classification but MUST NOT be transparently replayed.

#### Scenario: HTTP stream survives a temporary DNS outage

- **WHEN** a streaming Responses request fails DNS resolution before request dispatch
- **AND** DNS resolution recovers before the request budget expires
- **THEN** the proxy retries the request on the same account
- **AND** the downstream stream receives the recovered upstream response instead of a terminal network error

#### Scenario: Native WebSocket connect survives a temporary DNS outage

- **WHEN** a native Responses WebSocket request cannot open its upstream WebSocket because of a classified local network failure
- **AND** connectivity recovers before the request budget expires
- **THEN** the proxy opens the upstream WebSocket on the same account
- **AND** does not exhaust or exclude unrelated accounts

#### Scenario: Recovery remains bounded

- **WHEN** the local network does not recover before the configured request budget expires
- **THEN** the proxy terminates the request with `error.code = "upstream_request_timeout"` and message `"Proxy request budget exhausted"`
- **AND** does not extend the deadline or replay downstream-visible output

#### Scenario: Token refresh survives a temporary DNS outage

- **WHEN** token refresh for the selected account reports a classified process-network failure
- **AND** typed transport provenance proves the refresh POST was not dispatched
- **AND** connectivity recovers within the original request deadline
- **THEN** the proxy retries refresh on the same account
- **AND** does not record the network failure against the account

#### Scenario: Token refresh response failure is not replayed

- **WHEN** token refresh reports a classified process-network failure while reading the response or body
- **AND** the proxy cannot prove the refresh POST was not dispatched
- **THEN** the failure retains the account-neutral process-network code
- **AND** the proxy does not retry the possibly consumed rotating refresh token

#### Scenario: Ambiguous compact POST failure is not replayed

- **WHEN** a compact POST reports a classified process-network failure without typed pre-dispatch provenance
- **THEN** the compact failure retains the account-neutral process-network code
- **AND** the proxy does not replay, penalize, or exclude the selected account

#### Scenario: Serialized terminal network failure is not replayed

- **WHEN** an upstream stream emits a terminal response event carrying the process-network code
- **AND** the proxy cannot prove that request dispatch did not occur
- **THEN** the terminal event is surfaced without transparent replay
- **AND** the selected account's health remains unchanged

#### Scenario: Post-connect WebSocket network failure is not replayed speculatively

- **WHEN** an upstream WebSocket send or receive reports a classified process-network failure after the connection opened
- **AND** the proxy cannot prove that `response.create` was not delivered
- **THEN** the pending request fails with the account-neutral process-network code
- **AND** the proxy does not transparently replay the request

### Requirement: File-pinned compact refresh/connect failures fail closed

The proxy SHALL preserve file-owner routing during pre-visible refresh and
upstream-connect failure handling. If the pinned account cannot refresh or open
the upstream compact connection before any compact response is emitted, the proxy
MUST surface a stable upstream-unavailable failure for that request instead of
excluding the pinned account and replaying the compact request on another
account. This fail-closed rule applies only to file-pinned compact requests;
replayable compact/connect requests without a live file-id pin continue to use
the existing pre-visible forced-refresh and eligible-account failover behavior.

#### Scenario: file-pinned compact request fails closed on refresh transport failure

- **GIVEN** `file_pinned` was uploaded through `account_a` and its durable pin is live
- **AND** a compact request references `{"type": "input_file", "file_id": "file_pinned"}`
- **WHEN** `account_a` fails token refresh with a pre-visible transport or connection error
- **THEN** the proxy returns an upstream-unavailable error for that compact request
- **AND** it does not select another account for that request

#### Scenario: replayable compact request without file pins can still fail over

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the compact request has no live `input_file.file_id` routing pin
- **WHEN** the selected account fails before compact output is emitted and the
  failure is classified by an existing pre-visible failover rule
- **THEN** the proxy may exclude that account for the current request and try
  another eligible account

#### Scenario: retained file-backed bridge replay remains owner-bound

- **GIVEN** an HTTP bridge precreated request uses a proxy-injected
  `previous_response_id` anchor
- **AND** the retained retry-safe full body references an account-scoped
  uploaded file through `input_file.file_id` or file-backed `input_image`
- **WHEN** the bridge retries after an upstream close before visible output
- **THEN** the proxy keeps the anchored request owner-bound instead of stripping
  the anchor, excluding the owner, and replaying the file reference on another
  account
- **AND** if the file owner cannot be reselected, the retry fails closed instead
  of reconnecting the bridge on a replacement account

#### Scenario: verified owner refresh failover releases the failed stream lease

- **GIVEN** a streaming request selects the previous-response owner and holds an
  account stream lease
- **AND** a locally verified full resend permits failover after that owner fails
  refresh or connect before output is emitted
- **WHEN** the proxy excludes the failed owner and selects a replacement account
- **THEN** the failed owner's stream lease is released before replacement
  selection so the owner does not retain stale local pressure

### Requirement: Stale HTTP bridge previous-response aliases fail closed

The HTTP bridge MUST NOT treat a stale previous-response alias as a model
transition unless the indexed session's model is incompatible with the incoming
request. When a previous-response alias resolves to a closed or inactive session
for the same model and no durable recovery owner is available, the proxy MUST
surface the existing continuity-lost failure instead of creating or selecting a
replacement bridge.

#### Scenario: stale same-model previous-response alias fails closed

- **GIVEN** the previous-response index still points to an inactive HTTP bridge
  session for the same model
- **AND** no durable owner lookup is available for that response id
- **WHEN** a request arrives with that `previous_response_id`
- **THEN** the proxy fails closed with the stream-incomplete continuity error
- **AND** it does not create a replacement bridge for the stale response id

### Requirement: Cross-account bridge retries clear turn-state

When a pre-visible HTTP bridge request is proven safe to replay on another account, the proxy MUST clear the retired account's upstream and downstream turn-state before opening the replacement connection. The replacement handshake MUST NOT carry an `x-codex-turn-state` header learned from the excluded account.

#### Scenario: safe bridge replay excludes the stalled account

- **GIVEN** a pre-visible HTTP bridge request is proven safe to replay
- **WHEN** the failed bridge account is excluded before reconnect
- **THEN** the proxy clears the retired account's turn-state fields and header
- **AND** the replacement account receives no turn-state from the retired socket

### Requirement: Pre-visible unary refresh/connect failures fail over

For unary proxy requests that have not emitted downstream-visible output, the proxy MUST treat retryable token-refresh or upstream-connect transport failures as account-local transient failures.

This applies to Codex thread-goal requests, Codex control requests,
transcription requests, and file create/finalize requests. When another
eligible account is available within the request budget, the proxy MUST record
the failed account, exclude it from the current request, and retry the unary
operation on the fallback account. The proxy MUST NOT fail over strict
account-owner requests whose upstream resource is bound to the selected account.

#### Scenario: Unary refresh transport failure uses another account

- **GIVEN** at least two accounts are eligible for a Codex thread-goal, Codex
  control, transcription, or file-create request
- **AND** the selected account fails during token refresh or upstream connect
  with a retryable transient transport error before downstream-visible output
- **WHEN** another eligible account can complete the request within the request
  budget
- **THEN** the downstream request succeeds from the fallback account
- **AND** the failed account is recorded and excluded from further attempts for
  that request

#### Scenario: Strict file-owner refresh failure fails closed

- **GIVEN** a file-finalize request is pinned to the account that owns the file
- **AND** the pinned account fails during token refresh or upstream connect with
  a retryable transient transport error before downstream-visible output
- **WHEN** another account would otherwise be eligible for proxy traffic
- **THEN** the proxy fails the request with an upstream-unavailable error
- **AND** the proxy does not send the file-finalize operation through another
  account

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
request contains any `input_image` part in top-level input items, nested
message content, or tool output content, and send the request over the raw HTTP
Responses stream path. This bypass MUST happen after rejecting unsupported
uploaded-image references and MUST be limited to the current request; subsequent
text-only requests MAY continue using the HTTP responses bridge.

The raw HTTP path is the source of truth for image validation and upstream image
error semantics. The bridge MUST NOT hold image requests waiting for
`response.created` when upstream rejects an invalid inline image payload.

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains a nested content part with `type = "input_image"`
- **THEN** the request is sent through the raw HTTP stream path
- **AND** the HTTP responses bridge is not used for that request

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later text-only request can still use the HTTP responses bridge

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, codex-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** codex-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: No authorized account is available

- **WHEN** codex-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** codex-lb either continues normal account failover when safe or returns the original security-work authorization error when normal failover is exhausted or unsafe

#### Scenario: Pinned requests are not moved to another account

- **WHEN** a security-work authorization error occurs for a request pinned by file ownership or previous-response ownership
- **THEN** codex-lb MUST NOT replay the request on a different account
- **AND** the client receives the original security-work authorization failure.

#### Scenario: WebSocket replay releases the response-create gate

- **WHEN** a downstream websocket request is eligible for security-work replay
- **THEN** codex-lb releases the request's response-create gate before scheduling the replay
- **AND** the replay can acquire the gate instead of blocking behind the failed first attempt

### Requirement: HTTP bridge security retries fail closed after an anchor or output

For HTTP bridge requests, the service MUST retry security-work authorization on
another account only before `response.created` and before any upstream model
output. A buffered reasoning prelude counts as upstream model output even while
it is withheld from downstream pending the security decision. A permitted
file-free retry MUST select the replacement with cleared request and session
affinity, but MUST validate any raw legacy owner before changing the live
session or its durable owner generation. On success it MUST make exactly one
durable replacement claim before swapping the session, then clear or replace
the session affinity and local turn-state aliases. A legacy-owner conflict MUST
leave the original session open and unchanged. File-pinned requests MUST NOT
migrate.

#### Scenario: Created HTTP bridge response is not replayed

- **WHEN** an HTTP bridge request has emitted `response.created` before a
  security-work authorization denial
- **THEN** the service does not reconnect or resend the request on another
  account
- **AND** it forwards the original terminal error

#### Scenario: Deferred reasoning blocks replay

- **WHEN** an HTTP bridge request buffers a reasoning prelude before a
  security-work authorization denial
- **THEN** that prelude blocks account-switch replay and is not emitted before
  the terminal security decision

#### Scenario: Legacy owner conflict fails before replacement mutation

- **GIVEN** a session-header security retry selects an authorized replacement account
- **AND** the raw legacy affinity row belongs to a different account
- **WHEN** the service validates the replacement
- **THEN** it does not claim the durable session for the replacement
- **AND** it leaves the original account, upstream, owner generation, aliases, and open session unchanged

### Requirement: Responses request compatibility controls

The system SHALL accept OpenAI-compatible Responses request controls that clients may send for `/v1/responses` and `/backend-api/codex/responses` when those controls can be safely normalized before the ChatGPT-backed upstream request. Specifically, `truncation` values `"auto"` and `"disabled"` MUST pass request validation and MUST be omitted from the upstream payload because the current ChatGPT-backed path does not consume the field. Unsupported `truncation` values MUST still be rejected with HTTP 400.

#### Scenario: Truncation auto is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "auto"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

#### Scenario: Truncation disabled is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "disabled"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

### Requirement: HTTP bridge stale-session cleanup is bounded

The HTTP responses bridge MUST NOT hold the global bridge session registry lock
while awaiting operations that can block on a stale session's upstream websocket,
per-session pending lock, durable session repository, account lease release, or
other external cleanup work.

When stale bridge sessions are discovered during `/v1/responses`,
`/backend-api/codex/responses`, `/v1/responses/compact`, or
`/backend-api/codex/responses/compact` startup, the registry lock MAY be used to
remove closed or idle sessions from in-memory indexes, but potentially blocking
session close/fail-pending work MUST run after the lock is released or under a
bounded cleanup path. A wedged stale session MUST NOT prevent unrelated soft
HTTP Responses work from creating or reusing another bridge session.

Idle pruning MUST make pending-request decisions only while holding the
session's pending-request lock. If that lock cannot be acquired immediately,
the service MUST skip pruning that session instead of inferring that it is idle
from unlocked pending-request state.

If cleanup cannot complete within the bounded cleanup path, the service MUST log
a low-cardinality local bridge cleanup warning and continue protecting registry
progress. Requests that cannot safely proceed because a hard-continuity session
is unavailable MUST fail closed with an explicit local overload or continuity
error rather than silently hanging.

When a replacement bridge session claims the same durable key after stale local
session detachment, the durable owner generation MUST advance so that a late
cleanup from the stale local session cannot release or close the replacement
session's durable ownership. This MUST also apply when the detached local
session is retiring but still has visible in-flight requests and will release
its durable ownership later after draining. After a detached retiring session
finishes draining its visible requests, it MUST release its durable ownership
and account lease instead of only closing the upstream websocket.
If that retirement is initiated by the upstream-reader task after processing
the terminal upstream event, session close MUST NOT cancel or await the current
upstream-reader task itself.

When bridge capacity eviction removes an idle local session to admit a
replacement session, the evicted session's close MUST be awaited through a
bounded path before the replacement selects an account, so the evicted
session's account lease cannot cause a spurious no-account or local-capacity
failure.

If a request is cancelled while awaiting that pre-creation eviction close after
registering replacement session creation as in-flight, the service MUST fail or
remove the in-flight creation marker before propagating cancellation. Later
requests MUST NOT wait on an orphaned creation future that can never complete.

#### Scenario: wedged stale pending lock does not block fresh soft request

- **GIVEN** the HTTP responses bridge has an idle or stale local session whose
  pending-request lock does not complete promptly
- **WHEN** a new soft-affinity `/v1/responses` request starts bridge session
  selection
- **THEN** the global bridge registry lock is not held indefinitely by stale
  cleanup
- **AND** the stale session is not pruned based on unlocked pending-request
  state
- **AND** the new request either creates/reuses an eligible bridge session or
  returns an explicit bounded local error
- **AND** it does not hang before account selection or bridge create/reuse
  logging

#### Scenario: stale close runs outside registry lock

- **GIVEN** bridge startup identifies an idle stale session that must be closed
- **WHEN** closing that session awaits upstream-reader cancellation, websocket
  close, durable release, or account lease release
- **THEN** the global bridge registry lock is already released
- **AND** unrelated bridge startup requests can continue to inspect or mutate
  the registry

#### Scenario: stale durable release cannot fence out replacement owner

- **GIVEN** a stale or retiring bridge session for a durable key is replaced by
  a new local session after local detachment
- **WHEN** the stale session's bounded background close releases durable
  ownership after the replacement has claimed the same durable key
- **THEN** the stale release does not clear the replacement owner's durable
  lease
- **AND** follow-up requests for the replacement session do not receive a
  spurious bridge owner mismatch caused by the stale close

#### Scenario: detached retiring session releases resources after drain

- **GIVEN** a retiring bridge session was detached while visible requests were
  still draining
- **WHEN** those visible requests drain and the session is retired
- **THEN** the service releases the old session's durable ownership
- **AND** the service releases the old session's account lease
- **AND** upstream-reader-owned retirement does not self-cancel the current
  upstream reader task
- **AND** the detached session no longer holds bridge capacity until process
  exit

#### Scenario: LRU eviction releases lease before replacement account selection

- **GIVEN** the bridge is at local session capacity and an idle session is
  selected for LRU eviction
- **WHEN** a replacement bridge session is created after that eviction
- **THEN** the evicted session is closed through a bounded path before the
  replacement selects an account
- **AND** the evicted session's account lease does not cause the replacement to
  fail with a spurious no-account or local-capacity error

#### Scenario: cancellation during LRU close clears in-flight creation

- **GIVEN** the bridge is at local session capacity and an idle session is
  detached for LRU eviction before replacement creation
- **WHEN** the replacement request is cancelled while the bounded eviction close
  is still awaiting cleanup
- **THEN** the replacement in-flight creation marker is removed or failed before
  cancellation is propagated
- **AND** later requests for the same bridge key do not wait on that abandoned
  creation marker

### Requirement: Codex compaction triggers are bridged into compact output

When `POST /backend-api/codex/responses` receives a request whose top-level `input` array contains exactly one `{"type":"compaction_trigger"}` item as its final element, the proxy SHALL remove that trigger before calling upstream compaction handling and SHALL emit a raw SSE stream that contains exactly one compaction output item.

The stream MUST emit `response.created`, `response.output_item.added`, `response.output_item.done`, and `response.completed` in that order with monotonically increasing sequence numbers. The added event MUST expose the selected compaction item as in progress. The done event and terminal completed response MUST carry the same terminal `compaction` item. When the selected encrypted upstream compaction item carries a non-empty `id` or `status`, the synthetic stream MUST preserve those values with its `encrypted_content`; it MUST NOT generate a replacement item ID.

For Codex-affinity standalone compact requests, `POST /backend-api/codex/responses/compact` SHALL normalize an upstream remote-compaction-v2 response that includes historical message output plus a compaction summary into the single compact output item required by Codex clients. A non-empty upstream compaction item `id` or `status` MUST be preserved in that normalized output item.

OpenAI-style `/v1/responses/compact` is unchanged by this requirement.

#### Scenario: terminal trigger emits a complete compact lifecycle
- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one top-level `compaction_trigger`
- **THEN** the proxy strips the trigger and invokes compact handling
- **AND** it emits created, added, done, and completed events in that order
- **AND** their sequence numbers increase monotonically from zero
- **AND** the done event and completed response contain the same single terminal compaction item

#### Scenario: encrypted compaction item identity survives trigger streaming
- **WHEN** compaction handling for a terminal trigger returns encrypted content with a non-empty upstream `cmp_*` ID and terminal status
- **THEN** the added event exposes that ID with in-progress status
- **AND** the done event and completed response preserve the exact upstream ID, terminal status, and encrypted content
- **AND** the proxy does not synthesize a replacement item ID

#### Scenario: malformed trigger placement is rejected
- **WHEN** a `POST /backend-api/codex/responses` request contains a duplicated or non-terminal top-level `compaction_trigger` item
- **THEN** the proxy returns HTTP 400 with `invalid_request_error`
- **AND** it does not attempt upstream compaction handling

#### Scenario: Codex-affinity standalone compact normalizes remote v2 output
- **WHEN** a Codex-affinity `POST /backend-api/codex/responses/compact` request receives upstream output that contains historical message items and one compaction summary item
- **THEN** the JSON response body contains exactly one `output` item for that compaction summary
- **AND** the normalized item preserves the compaction summary's non-empty upstream ID and status
- **AND** it does not expose historical message items as standalone compact output

### Requirement: Request logs expose upstream Responses transport
For streaming Responses proxy requests, persisted request logs MUST distinguish the downstream client transport from the upstream egress transport by recording the upstream transport in `request_logs.upstream_transport` while preserving `request_logs.transport` as the downstream client transport.

#### Scenario: downstream HTTP single-shot records upstream HTTP
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing chooses upstream HTTP for a single-shot Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"http"`

#### Scenario: downstream HTTP sticky records preserved auto upstream mode
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing keeps the base upstream `"auto"` mode for a sticky Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"auto"`

#### Scenario: historical or unrelated rows tolerate missing upstream transport
- **GIVEN** a request log row predates upstream transport persistence or belongs to a request kind that does not know its upstream transport
- **WHEN** the row is read
- **THEN** `upstream_transport` MAY be null
- **AND** the existing request-log response MUST remain valid

### Requirement: Request Logs API returns upstream transport
The Request Logs API MUST include `upstream_transport` on each request log entry so operators and dashboards can query upstream egress transport without overloading the existing downstream `transport` field.

#### Scenario: request logs response includes upstream transport
- **GIVEN** a persisted request log has `transport = "http"` and `upstream_transport = "auto"`
- **WHEN** a dashboard client fetches request logs
- **THEN** the returned entry includes `transport: "http"`
- **AND** the returned entry includes `upstream_transport: "auto"`

### Requirement: Upstream transport decisions emit low-cardinality metrics
Streaming Responses proxy requests MUST emit a low-cardinality Prometheus counter for upstream transport decisions. The metric MUST NOT include request id, account id, API key id, model, prompt cache key, or other high-cardinality identifiers.

#### Scenario: transport decision counter labels are bounded
- **WHEN** a streaming Responses request completes or terminates with an error
- **THEN** `codex_lb_upstream_transport_decisions_total` is incremented once
- **AND** its labels include only `downstream_transport`, `upstream_transport`, `policy`, `sticky`, and `status`
- **AND** `status` is `"success"` or `"error"`

### Requirement: Raw Responses streams require a terminal SSE event for success

For raw HTTP streaming Responses attempts, the proxy MUST NOT record request-log
status `success` or mark the selected account successful unless the stream
observed a terminal SSE event: `response.completed`, `response.failed`,
`response.incomplete`, or `error`. This requirement applies even when the
upstream HTTP response status was 200 because the stream body remains part of
the request outcome.

If the upstream iterator ends before a terminal event, the proxy MUST surface a
terminal `response.failed` SSE event with error code `stream_incomplete`, record
the request-log row as an upstream `stream_incomplete` error, and apply the
normal transient upstream account-health signal. If the downstream client
cancels or disconnects before a terminal event, the proxy MUST record the
request-log row with status `cancelled`, downstream error code
`client_disconnected`, and downstream failure metadata, and MUST NOT penalize
the upstream account.

#### Scenario: Raw stream upstream EOF is not successful

- **GIVEN** a raw HTTP streaming Responses request has emitted non-terminal SSE
  data
- **WHEN** the upstream stream ends before `response.completed`,
  `response.failed`, `response.incomplete`, or `error`
- **THEN** the downstream stream receives a terminal `response.failed` event
  with error code `stream_incomplete`
- **AND** the request log stores status `error`, error code
  `stream_incomplete`, and upstream failure metadata
- **AND** the selected account receives a transient upstream failure signal

#### Scenario: Raw stream downstream cancellation is client-side

- **GIVEN** a raw HTTP streaming Responses request has not observed a terminal
  SSE event
- **WHEN** the downstream client cancels or disconnects from the stream
- **THEN** the request log stores status `cancelled`, error code
  `client_disconnected`, and downstream failure metadata
- **AND** the selected account is not penalized for the client-side close

### Requirement: Responses SSE parsing uses only CR/LF line boundaries

When parsing streamed Responses Server-Sent Events, the service MUST treat only
CR (`\r`), LF (`\n`), and CRLF (`\r\n`) as SSE line boundaries. The parser MUST
NOT split a `data:` field on other Unicode line-boundary characters such as
U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR when those characters appear
inside the payload value. Multi-line `data:` fields delimited by CR, LF, or CRLF
MUST continue to be joined with `\n` before JSON decoding.

The streaming HTTP receive path MUST also treat CR-only blank lines (`\r\r`) as
complete SSE event separators, and any normalization of legacy event aliases
MUST preserve the event block's original CR, LF, or CRLF terminator style.

#### Scenario: Unicode separators inside JSON strings are preserved

- **WHEN** an upstream Responses SSE event contains a `data:` JSON payload whose
  string value includes unescaped U+2028 or U+2029
- **THEN** the parser preserves those characters inside the JSON string
- **AND** the event remains available to downstream response-event processing

#### Scenario: CR/LF-delimited multi-line data still joins

- **WHEN** an upstream Responses SSE event contains multiple `data:` lines
  delimited by CR, LF, or CRLF
- **THEN** the parser joins the field values with `\n`
- **AND** continues JSON decoding against the joined payload

#### Scenario: CR-only event separators dispatch complete events

- **WHEN** the HTTP streaming receive path receives an upstream SSE event ending
  in a CR-only blank line
- **THEN** it dispatches that event without waiting for EOF or an LF delimiter
- **AND** legacy event alias normalization preserves the CR-only blank-line
  terminator

### Requirement: Timed-out startup probes MUST settle first-item task exceptions

The proxy MUST retrieve eventual first-item task exceptions when a Responses or
chat-completions startup error probe times out while its first-item task is
still running and the returned stream is abandoned before iteration resumes.
This MUST prevent unhandled asyncio task diagnostics such as `Task exception was
never retrieved` or shielded-future exception logs for upstream
`ProxyResponseError` failures that arrive after the probe timeout.

If the returned stream is consumed later, the task result or exception MUST
remain observable through normal stream iteration.

#### Scenario: Abandoned timed-out probe consumes first-item exception

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **AND** the first-item task later raises `ProxyResponseError`
- **WHEN** the request path abandons the returned stream before consuming that task
- **THEN** the event loop does not emit an unhandled task-exception diagnostic
- **AND** task ownership is settled without changing the client-visible result

#### Scenario: Consumed timed-out probe preserves stream behavior

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **WHEN** the caller later iterates the returned stream
- **THEN** the first task's result or exception is still yielded or raised through the returned stream

### Requirement: Codex installation metadata is account-owned

For Codex response-create upstream requests, the service MUST attach a
server-owned per-account Codex installation id to upstream client metadata when
an account is selected. Inbound client-supplied Codex installation id headers or
metadata MUST NOT be trusted as the account installation id. Existing unrelated
client metadata such as turn metadata MUST be preserved.

#### Scenario: Inbound installation id is replaced

- **GIVEN** an account has a stored Codex installation id
- **AND** a client sends response-create metadata with a different
  `x-codex-installation-id`
- **WHEN** the request is forwarded upstream
- **THEN** the upstream metadata contains the account's stored installation id
- **AND** preserves unrelated metadata entries

#### Scenario: Inbound installation id header is stripped

- **WHEN** a client sends `X-Codex-Installation-Id`
- **THEN** the upstream request does not forward that header as a trusted
  client-supplied identity

### Requirement: Compact payloads omit unsupported client metadata

Compact request payload normalization MUST remove `client_metadata` before
forwarding compact requests upstream.

#### Scenario: Compact strips client metadata

- **WHEN** a compact payload includes `client_metadata`
- **THEN** the upstream compact payload omits it

### Requirement: Preserve raw backend stream error frames when contract mode is disabled

The proxy MUST preserve raw backend stream error frames when contract mode is
disabled. When the proxy serves `POST /backend-api/codex/responses` with
`enforce_openai_sdk_contract=False`, it MUST forward upstream HTTP SSE frames
with `type: "error"` unchanged on the stream. In this mode, no
`response.failed` synthesis is allowed before `yield` for those upstream frames.

#### Scenario: Raw backend error passthrough

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling sets `enforce_openai_sdk_contract=False`
- **WHEN** the proxy forwards that upstream event in the public stream
- **THEN** the downstream event MUST remain an `error` event
- **AND** `sequence_number`, `error_type`, and message fields from upstream must remain unchanged
- **AND** the event SHOULD NOT be rewritten into `response.failed` in the same stream step

### Requirement: Keep default contract shaping enabled unless explicitly disabled

The proxy MUST keep default contract shaping enabled unless explicitly
disabled. For backward-compatible behavior, when
`enforce_openai_sdk_contract` is omitted or `True`, current error-shaping
behavior MUST remain in place and convert error-type SSE frames as defined by
existing `responses-api-compat` contracts.

#### Scenario: Default public contract still emits response.failed

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling omits `enforce_openai_sdk_contract` or sets it to `True`
- **WHEN** the proxy forwards that upstream event
- **THEN** the downstream event MUST be normalized to `response.failed`

### Requirement: Retry-safe stale WebSocket anchors replay before owner fail-closed handling
When a direct Responses WebSocket request has a prepared retry-safe fresh upstream request body without `previous_response_id`, the service MUST use that replay path for upstream `previous_response_not_found` before applying preferred-owner unavailable handling. This applies when the stale anchor was proxy-injected from session continuity as well as when a client full-resend was classified retry-safe.

#### Scenario: proxy-injected stale anchor has a preferred owner
- **GIVEN** a WebSocket request has `previous_response_id`, a preferred owner account, and `fresh_upstream_request_is_retry_safe` with a no-anchor replay body
- **WHEN** upstream emits `previous_response_not_found` before `response.created`
- **THEN** the service reconnects and replays the prepared no-anchor request
- **AND** it does not rewrite the turn to `previous_response_owner_unavailable`

### Requirement: Codex WebSocket prewarm completions are classified separately
For a direct Responses WebSocket, the service MUST treat Codex turn metadata received on the HTTP handshake as connection-scoped metadata rather than applying its `request_kind` to every `response.create` frame. The service MUST classify an individual turn as `prewarm` when the connection metadata is `prewarm` and either that turn carries `generate: false` or its completed usage reports zero output tokens. Other turns on the same connection MUST be classified as `normal`.

Request logs for direct Responses WebSocket turns MUST persist the connection-scoped value separately as `connection_request_kind`. Empty-output prewarm completions MUST NOT update account success state or previous-response ownership, while still allowing the upstream terminal frame to pass through.

#### Scenario: generated turn on a prewarm-opened connection is normal
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** a later `response.create` does not carry `generate: false` and upstream completes it with non-zero output tokens
- **THEN** the request log records `request_kind` as `normal`
- **AND** the request log records `connection_request_kind` as `prewarm`
- **AND** the completion remains eligible to update account success state and previous-response ownership

#### Scenario: empty prewarm completion does not look like user turn progress
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** a `response.create` carries `generate: false` or upstream completes it with zero output tokens
- **THEN** the request log records `request_kind` as `prewarm`
- **AND** the request log records `connection_request_kind` as `prewarm`
- **AND** the service does not mark the account successful for that completion
- **AND** the service does not remember the response id as a usable previous-response owner

#### Scenario: failed generated turn on a prewarm-opened connection is normal
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **AND** a later `response.create` does not carry `generate: false`
- **WHEN** that turn fails before completed usage is available
- **THEN** the request log records `request_kind` as `normal`
- **AND** the request log records `connection_request_kind` as `prewarm`

### Requirement: Codex compact requests are bounded by the proxy request budget
When `/backend-api/codex/responses/compact` is called for Codex auto-compaction, the service MUST bound the upstream compact call by the remaining proxy compact request budget even when no explicit upstream compact timeout is configured. The service MUST preserve Codex turn metadata `request_kind` in compact request logs so auto-compaction failures are distinguishable from normal user turns.

#### Scenario: auto-compaction cannot hang past the proxy budget
- **GIVEN** a Codex compact request carries `x-codex-turn-metadata` with `request_kind: "compaction"`
- **AND** no explicit upstream compact timeout is configured
- **WHEN** the service calls upstream
- **THEN** the upstream call receives both connect and total timeout overrides from the remaining compact request budget
- **AND** the request log records `request_kind` as `compaction`

### Requirement: Responses Lite signaling is derived from the normalized body

The service MUST accept Responses and compact requests that include
`X-OpenAI-Internal-Codex-Responses-Lite`, but MUST remove that inbound header
case-insensitively before generic upstream-header forwarding. The service MUST
NOT strip unrelated OpenAI SDK telemetry headers solely because they start with
`x-openai-`.

When an input array contains an item with `type = "additional_tools"`,
instruction normalization MUST leave the entire input array and top-level
`instructions` field unchanged. In particular, neither the tool item nor an
adjacent developer instructions message may be extracted from the native Lite
input prefix. The presence of the `additional_tools` item in the normalized
input array MUST be the authoritative signal that the request uses Responses
Lite.

If compact-request size handling trims oversized conversation history, it MUST
retain the `additional_tools` item and its immediately following developer
instructions message. The resulting compact payload MUST therefore retain the
body signal needed to synthesize the canonical Lite header.

For a Responses Lite body, upstream HTTP Responses and compact requests MUST
include the canonical `x-openai-internal-codex-responses-lite: true` header.
Upstream websocket handshakes MUST omit that header and each websocket
`response.create` body MUST instead include
`client_metadata.ws_request_header_x_openai_internal_codex_responses_lite = "true"`.
For a non-Lite HTTP body, the proxy MUST omit the synthesized HTTP header. A
websocket marker on an incremental frame without the full Lite input prefix MAY
remain only when the same request continuity state previously received
`response.created` for a Lite request derived from `additional_tools` using the
same effective upstream model, and the frame's `previous_response_id` references
the response ID recorded by the most recent such Lite acceptance. A frame
without a `previous_response_id`, or one referencing any other response, MUST
NOT receive trusted Lite treatment. The recorded acceptance ID MUST be the
response ID exposed downstream: when a transparent replay suppresses its
`response.created` and keeps rewriting events to the originally visible
response ID, Lite continuity records that visible ID rather than the hidden
upstream replay ID. The effective model comparison MUST occur
after alias normalization and API-key enforcement, and a merely prepared request
MUST NOT establish or clear trusted Lite continuity. Trusted state MUST update
in upstream request-acceptance order rather than terminal-event completion
order, and acceptance of a non-Lite request MUST NOT clear previously recorded
Lite continuity.
An accepted `generate = false` prewarm derived from an `additional_tools` prefix
MUST establish the same trusted continuity because a later request MAY reuse its
response ID without repeating that prefix.
A transparent fresh full-resend replay that clears `previous_response_id` (for
example after an upstream previous-response miss) severs that linkage, so the
replayed request MUST NOT carry the reserved marker unless its own input
contains the `additional_tools` prefix. Acceptance of such a replay MUST
reflect the replayed body: a marker-stripped replay MUST NOT be recorded as a
Lite acceptance (later frames referencing the replay's response ID are not
trusted), while a replay whose input retains the `additional_tools` prefix
MUST re-establish trusted Lite continuity.
Otherwise, the proxy MUST strip the reserved client-metadata marker. The
HTTP-to-websocket bridge MUST preserve its internally derived canonical marker
when it trims an already-stored input prefix or rebuilds the request during
forwarding or retry, even if the remaining input delta has no `additional_tools`
item.

#### Scenario: Instruction normalization preserves Lite tools and tool history

- **WHEN** a request input contains an `additional_tools` item, developer text,
  custom tool calls, and `custom_tool_call_output` items
- **THEN** top-level `instructions` remains unchanged
- **AND** the developer text, `additional_tools`, custom calls, and outputs all
  remain in their original input order

#### Scenario: HTTP and compact synthesize Lite only from the body

- **WHEN** a normalized HTTP Responses or compact payload contains an
  `additional_tools` input item
- **THEN** the upstream request includes
  `x-openai-internal-codex-responses-lite: true`
- **AND** the original inbound Lite header value is not forwarded verbatim

#### Scenario: Compact trimming retains the Lite prefix

- **GIVEN** an oversized Responses Lite compact input whose tool bundle exceeds
  the normally retained head budget
- **WHEN** compact size handling trims conversation history
- **THEN** the `additional_tools` item and adjacent developer instructions stay
  in their original order
- **AND** the upstream compact request includes the canonical Lite header

#### Scenario: Websocket uses a per-request Lite marker

- **WHEN** a websocket `response.create` payload contains an `additional_tools`
  input item
- **THEN** the upstream websocket handshake omits the Lite header
- **AND** the forwarded `response.create` payload contains the canonical
  per-request Lite client-metadata marker

#### Scenario: HTTP bridge trimming preserves Lite metadata

- **GIVEN** an HTTP Responses Lite request whose stored input prefix contains
  the `additional_tools` item
- **WHEN** the HTTP-to-websocket bridge trims that prefix and forwards only the
  new input delta
- **THEN** the forwarded `response.create` payload still contains the canonical
  per-request Lite client-metadata marker

#### Scenario: Incremental websocket marker requires trusted Lite continuity

- **GIVEN** a websocket request received `response.created` after establishing
  Lite mode from an `additional_tools` prefix for its effective upstream model
- **WHEN** a later same-model incremental frame contains the canonical marker,
  omits the already-known prefix, and its `previous_response_id` references the
  accepted Lite response
- **THEN** the forwarded frame retains the canonical marker
- **BUT WHEN** a request for another model supplies that marker without a Lite
  prefix or trusted same-model continuity
- **THEN** the proxy strips the marker
- **BUT WHEN** a same-model frame supplies that marker without a
  `previous_response_id`, or with one referencing a response other than the
  accepted Lite response
- **THEN** the proxy strips the marker
- **AND** the recorded Lite continuity remains available to later frames that
  do reference the accepted Lite response

#### Scenario: Suppressed-created replay keeps Lite continuity on the visible id

- **GIVEN** a Lite websocket request whose `response.created` was already sent
  downstream when the upstream connection is lost
- **WHEN** the proxy transparently replays the request, suppresses the new
  `response.created`, and rewrites downstream events to the original visible
  response id
- **THEN** a later same-model marker-only frame whose `previous_response_id`
  references the visible response id keeps the trusted marker
- **BUT WHEN** a frame references the hidden upstream replay id instead
- **THEN** the proxy strips the marker

#### Scenario: Fresh replay of a trusted incremental frame drops the marker

- **GIVEN** a trusted marker-only incremental websocket frame whose
  self-contained multi-item input yields a transparent fresh full-resend replay
- **WHEN** upstream reports the referenced previous response as not found and
  the proxy replays the request without `previous_response_id`
- **THEN** the replayed request omits the reserved client-metadata marker
- **AND** the accepted replay is not recorded as a Lite acceptance, so a later
  same-model frame carrying the marker with `previous_response_id` referencing
  the replay's response is not trusted and has its marker stripped
- **BUT WHEN** the replayed input itself contains the `additional_tools` prefix
- **THEN** the replayed request retains the canonical marker
- **AND** the accepted replay re-establishes trusted Lite continuity for later
  frames referencing its response ID

#### Scenario: Accepted Lite prewarm authorizes incremental reuse

- **GIVEN** a same-model Lite prewarm containing `additional_tools` receives
  `response.created`
- **WHEN** Codex reuses that response ID in a later frame with the canonical
  marker but without the already-sent Lite prefix
- **THEN** the forwarded frame retains the canonical marker whether its input
  delta is empty or contains new user input

#### Scenario: Stale inbound headers do not enable a non-Lite request

- **WHEN** an HTTP request has no `additional_tools` input item but includes an
  inbound Lite header
- **THEN** the upstream HTTP request omits the Lite signal
- **AND** existing Codex continuity and unrelated OpenAI telemetry headers are
  preserved

### Requirement: WebSocket tool-output deltas are not fresh-retryable

The service MUST NOT replay a direct WebSocket Responses request as a fresh turn
without the previous-response anchor when it includes `previous_response_id` and
only carries tool output items for tool calls that are not present in the same
payload after an upstream `previous_response_not_found`.

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn

- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses`
  follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`,
  `custom_tool_call_output`, or `apply_patch_call_output` items without their
  matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a
  response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without
  `previous_response_id`

### Requirement: Ultra reasoning effort is aliased to max on the upstream wire

The proxy MUST forward any outbound upstream Responses payload whose `reasoning.effort` resolves to `ultra` — whether requested by the client or injected by API-key reasoning enforcement — with `reasoning.effort: "max"`. `ultra` is a client-plane reasoning effort: GPT-5.6 Sol and Terra advertise it
in their catalog entries, but the reference Codex client rewrites it to `max`
before building the upstream Responses request
(`reasoning_effort_for_request` in codex-rs `core/src/client.rs` at release
rust-v0.144.1); its additional effect (proactive multi-agent mode) is purely
client-side. Source-routed chat-completions
payloads with an enforced `ultra` effort MUST likewise forward `max`. Code
paths that build upstream Responses payloads directly instead of passing
through the proxy request-policy rewrite — such as automation compact pings —
MUST apply the same aliasing before dispatch, while persisted automation
configuration and run history keep the configured client-plane `ultra` value.
`max`
and `xhigh` MUST be forwarded verbatim (no `max` → `xhigh` aliasing exists
upstream).

#### Scenario: Client-requested ultra forwards as max

- **WHEN** a client sends a Responses request for `gpt-5.6-sol` with `reasoning: {"effort": "ultra"}`
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Enforced ultra forwards as max

- **GIVEN** an API key configured with `enforcedReasoningEffort: "ultra"`
- **WHEN** a request is proxied with that API key
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Automation compact ping with ultra dispatches max

- **GIVEN** an automation configured with model `gpt-5.6-sol` and reasoning effort `ultra`
- **WHEN** an automation run dispatches its compact ping upstream
- **THEN** the dispatched compact payload uses `reasoning.effort: "max"`
- **AND** the stored automation run history keeps the configured `ultra` effort

#### Scenario: Max is forwarded verbatim

- **WHEN** a client sends a Responses request with `reasoning: {"effort": "max"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "max"`

### Requirement: Source-routed Responses tools are capability-filtered

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST forward `function` tools unchanged and MUST drop non-`function` tools the
source model has not declared support for. A source model declares support in
its `raw_metadata_json`: `"supports_search_tool": true` keeps web-search tools
(`web_search`, including the `web_search_preview` alias), and
`"experimental_supported_tools"` MAY list additional supported tool types.
When only some tools are dropped, a `tool_choice` that references a dropped
tool MUST be removed so the forwarded payload never names a tool that is not
present; `function`-typed choices MUST be preserved. When all tools are
dropped, `tools`, `tool_choice`, and `parallel_tool_calls` MUST be removed
together. Whenever a hosted tool is dropped, `include` entries specific to
that tool type (for example `web_search_call.*` for `web_search`,
`file_search_call.*` for `file_search`, `code_interpreter_call.*` for
`code_interpreter`, and `computer_call_output.*` for computer-use tools) MUST
be pruned from the forwarded payload; non-tool-specific entries (for example
`reasoning.encrypted_content`) MUST be kept, and the `include` field MUST be
removed entirely when pruning empties it. This filtering MUST apply on every
source-routed Responses surface (`/backend-api/codex/responses` and
`/v1/responses`).

#### Scenario: Codex-only tools are dropped for a plain source model

- **GIVEN** a Responses-capable source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `namespace` tool, and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool

#### Scenario: Search-capable source models keep web-search tools

- **GIVEN** a source model whose `raw_metadata_json` sets `"supports_search_tool": true`
- **WHEN** a Responses request with a `function` tool and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains both tools
- **AND** a `tool_choice` of `{"type": "web_search"}` is preserved

#### Scenario: tool_choice referencing a dropped tool is removed

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `tool_choice` `{"type": "web_search"}` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload contains no `tool_choice` key

#### Scenario: include entries of a dropped tool are pruned

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `include` `["web_search_call.action.sources", "reasoning.encrypted_content"]` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload's `include` contains only `"reasoning.encrypted_content"`

#### Scenario: Dropping every tool removes the tool-only fields

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request whose tools are all unsupported is forwarded to it
- **THEN** the forwarded payload contains no `tools`, `tool_choice`, or `parallel_tool_calls` keys

### Requirement: Source request overrides apply without clobbering proxy-owned keys

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST apply the model's `source_request_overrides` from `raw_metadata_json` to
the forwarded payload. The `options` override MUST merge key-wise into any
client-sent `options` object, with override values winning per key. The
overrides MUST NOT change the `model` key (owned by source selection) or the
`stream` key (owned by the proxy's response-handling mode).

#### Scenario: Ollama options are injected into the forwarded payload

- **GIVEN** a source model whose overrides are `{"options": {"num_ctx": 32768}}`
- **WHEN** a Responses request is forwarded to the source
- **THEN** the forwarded payload contains `"options": {"num_ctx": 32768}`

#### Scenario: model and stream overrides are ignored

- **GIVEN** a source model whose overrides contain `"model": "other-model"` and `"stream": false`
- **WHEN** a streaming Responses request for slug `local-model` is forwarded to the source
- **THEN** the forwarded payload keeps `model` as the routed source model
- **AND** the forwarded payload keeps `stream` as `true`

### Requirement: Interrupted tool calls receive synthetic outputs on anchored follow-ups
The service MUST track tool-call items completed by a streamed response that may still require a tool output — `function_call`, `custom_tool_call`, and `apply_patch_call` — together with each call's item type. When a follow-up `response.create` anchors on that completed response via `previous_response_id` and its input omits an output item for a tracked call id, the service MUST prepend a synthetic interrupted output item whose type matches the originating call type (`function_call` -> `function_call_output`, `custom_tool_call` -> `custom_tool_call_output`, `apply_patch_call` -> `apply_patch_call_output`) before forwarding the request upstream. This applies to the direct WebSocket route and to the HTTP responses bridge session path.

#### Scenario: interrupted custom tool call on the WebSocket route
- **GIVEN** a WebSocket `response.create` turn completes with a `custom_tool_call` item whose output was never sent (the turn was interrupted)
- **WHEN** the next `response.create` on the same session references that response via `previous_response_id` without a `custom_tool_call_output` for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input
- **AND** the follow-up does not fail with an upstream `No tool output found for custom tool call` error

#### Scenario: interrupted custom tool call on the HTTP bridge
- **GIVEN** an HTTP bridge session completes a response containing a `custom_tool_call` item whose output was never sent
- **WHEN** the next bridge request anchors on that response id (client-sent or proxy-injected `previous_response_id`) without an output item for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input

#### Scenario: interrupted function call keeps existing output type
- **WHEN** the pending tool call recorded from the previous response is a `function_call`
- **THEN** the synthetic interrupted output item is a `function_call_output` (existing behavior preserved)

#### Scenario: follow-up that carries the tool output is not modified
- **WHEN** the anchored follow-up input already contains a `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` item for a pending call id
- **THEN** the service does not inject a synthetic output for that call id

#### Scenario: injected bridge outputs stay subject to the request size guard
- **GIVEN** an HTTP bridge follow-up whose serialized `response.create` is close to the upstream byte limit
- **WHEN** synthetic interrupted outputs are injected
- **THEN** the service prepares the upstream request from the injected payload so the `response.create` slim/size guard runs against the bytes actually sent upstream
- **AND** an over-limit injected request is rejected locally with `payload_too_large` instead of being forwarded upstream

#### Scenario: stored input context reflects the injected upstream input
- **WHEN** an HTTP bridge follow-up gains synthetic interrupted outputs
- **THEN** the input item count, input fingerprint, and request usage budget recorded for the request are computed from the injected upstream-shaped input, so later full-resend/anchor comparisons on the same bridge session match what upstream actually stored

#### Scenario: unfingerprinted input turns keep the WebSocket continuity anchor
- **GIVEN** a WebSocket turn whose request input yields no prefix fingerprint (a string input — normalized to a single user message at request validation — or an empty input list)
- **WHEN** the response completes with pending tool-call items
- **THEN** the continuity state still records the completed response id and the pending tool-call metadata for all tracked call types, clearing only the prefix count/fingerprint pair
- **AND** a follow-up that anchors on that response id receives the synthetic interrupted outputs instead of leaking the upstream missing-tool-output 400

#### Scenario: local previous-response recovery retry keeps injected outputs
- **GIVEN** an HTTP bridge submit whose payload gained synthetic interrupted outputs and which fails before yielding with a previous-response continuity error
- **WHEN** the local recovery path re-prepares the anchored retry request
- **THEN** the synthetic interrupted outputs are re-injected from the failed session's pending tool-call state, so the recovered submit does not reintroduce the upstream missing-tool-output failure

#### Scenario: replayed apply_patch prefix is trimmed on anchored bridge follow-ups
- **GIVEN** an HTTP bridge follow-up that anchors via `previous_response_id` and replays a prior `apply_patch_call` item (marked as response output) followed by its `apply_patch_call_output`
- **WHEN** the bridge trims the previous-response prefix already covered by the anchor
- **THEN** `apply_patch_call` and `apply_patch_call_output` items are recognized by the trim exactly like the `function_call` and `custom_tool_call` variants, matching the WebSocket route's replay trim

#### Scenario: owner-forward failover recovery injects from local session state when available
- **GIVEN** a multi-instance bridge where an anchored follow-up is forwarded to the remote owner instance and the relay fails before yielding any bytes
- **WHEN** the local instance recovers by rebinding a local bridge session and resubmitting the anchored request
- **THEN** the service injects synthetic interrupted outputs when the rebound local session still holds the pending tool-call state for the anchored response id (for example after ownership flapped back to this instance)

#### Scenario: owner-forward failover recovery without local pending state is a known bounded gap
- **GIVEN** the same owner-forward failure, where the pending tool-call metadata exists only in the remote owner instance's memory (the durable bridge store does not persist pending call ids)
- **WHEN** the local recovery rebinds a fresh session that has no pending tool-call state
- **THEN** the anchored recovery request is resubmitted unmodified, without fabricated tool outputs (matching pre-injection behavior)
- **AND** if upstream rejects it with a missing-tool-output error, the extended classifier masks it as a retryable continuity failure instead of surfacing the raw upstream 400

### Requirement: Missing-tool-output classification covers all tool call variants
The service MUST classify an upstream `invalid_request_error` with `param=input` whose message starts with `No tool output found for function call call_`, `No tool output found for custom tool call call_`, `No tool output found for apply patch call call_`, or `No tool output found for tool search call call_` as a missing-tool-output continuity error, so the existing masking and retry recovery paths engage instead of forwarding the raw upstream 400 downstream. The hosted `No tool output found for web search call` wording MUST NOT be classified, because a `web_search_call` is executed upstream and carries no client-addressable tool output.

#### Scenario: custom tool call variant is masked on the HTTP bridge
- **WHEN** upstream emits `invalid_request_error` with `param=input` and message `No tool output found for custom tool call call_x`
- **AND** the pending bridge request carries `previous_response_id`
- **THEN** the service rewrites the error to a retryable `stream_incomplete` continuity failure
- **AND** the raw upstream message and call id are not exposed downstream

#### Scenario: tool search call variant is masked on the HTTP bridge
- **WHEN** upstream emits `invalid_request_error` with `param=input` and message `No tool output found for tool search call call_x`
- **AND** the pending bridge request carries `previous_response_id`
- **THEN** the service rewrites the error to a retryable `stream_incomplete` continuity failure
- **AND** the raw upstream message and call id are not exposed downstream

#### Scenario: hosted web search wording stays unclassified
- **WHEN** upstream emits `invalid_request_error` with `param=input` and a message starting `No tool output found for web search call`
- **THEN** the service does not treat it as a missing-tool-output continuity error

### Requirement: Non-message system and developer input items are preserved

When normalizing Responses or compact request `input`, the service MUST only
hoist items that are instruction messages — `system`/`developer`-role items
whose `type` is omitted or `"message"` — into the top-level `instructions`
field. Any `system`/`developer`-role input item carrying any other `type`
value, including item types the service does not model, MUST be forwarded
upstream unchanged and in its original input position. This preservation MUST
hold both when the request is validated and when the request is serialized for
upstream delivery, and it exempts the item from input sanitization: keys such
as `reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call`
MUST NOT be stripped from a preserved item. When compact requests exceed the
upstream input budget and
the service trims the input middle, preserved non-message `system`/`developer`
items MUST be treated as trim anchors and retained in the trimmed payload
rather than replaced by the trim marker. When a non-message
`system`/`developer` item is preserved and the request carries no top-level
`instructions` and no hoistable instruction messages, the service MUST default
`instructions` to the empty string so the request still validates and
forwards. Requests whose input contains an `additional_tools` item remain
governed by the Responses Lite rule that leaves the entire input array and
top-level `instructions` unchanged.

#### Scenario: unknown non-message developer input item survives normalization

- **WHEN** a Responses or compact request `input` contains a typed, non-message
  item such as `{"type": "future_directive", "role": "developer", ...}`
  alongside developer instruction messages and user messages
- **THEN** the developer instruction messages are hoisted into `instructions`
- **AND** the `future_directive` item remains in `input` unchanged, in its
  original position
- **AND** the upstream-serialized payload retains the item unchanged

#### Scenario: preserved directive keeps reasoning and tool-call keys

- **WHEN** a Responses or compact request `input` contains a typed,
  non-message `system`/`developer` item carrying keys the interleaved
  reasoning sanitizer strips from message items (such as
  `reasoning_content`, `reasoning_details`, `tool_calls`, or `function_call`)
- **THEN** the item is retained byte-identical after validation
- **AND** the upstream-serialized payload retains the item byte-identical

#### Scenario: directive-only request without instructions still validates

- **WHEN** a Responses or compact request omits top-level `instructions` and
  its `input` contains only a typed, non-message `system`/`developer` item
  (such as `{"type": "future_directive", "role": "developer", ...}`) alongside
  user messages
- **THEN** the request validates with `instructions` defaulted to `""`
- **AND** the directive item remains in `input` unchanged, including in the
  upstream-serialized payload

#### Scenario: preserved directive survives compact input trimming

- **WHEN** a compact request is large enough to trigger upstream input
  trimming and its input middle contains a typed, non-message
  `system`/`developer` item such as
  `{"type": "future_directive", "role": "developer", ...}`
- **THEN** the trimmed upstream payload retains the item unchanged
- **AND** the item is not replaced by the trim marker

#### Scenario: typeless system messages keep hoisting behavior

- **WHEN** an OpenAI-compatible client sends `input` containing
  `{"role": "system", "content": "sys"}` without a `type` field
- **THEN** that item is hoisted into `instructions` as before

### Requirement: Responses Lite follow-up transformations fail closed

After a request is classified as Responses Lite shaped, the service MUST preserve required Lite state through compact preparation, MUST validate the final transformed compact input against the upstream JSON wire budget, MUST reject policy rewrites to catalog-confirmed non-Lite models, and MUST suppress replayed code-mode side effects without collapsing distinct call identities. Compact trimming MAY omit a complete terminal non-state, non-side-effecting tool pair only when the pair plus required anchors and trim markers cannot fit the upstream wire budget. A latest output anchored by `previous_response_id` or a non-empty `conversation` remains required only when its matching call is absent from supplied input. A supplied call matches an output only when both `call_id` and the function/custom/apply-patch protocol variant are compatible. An unmatched latest tool call and a terminal tool call or matching pair classified as side-effecting by the canonical tool-safety classifier remain required compact context. These guards MUST NOT weaken the body-derived Lite signal or trusted previous-response linkage rules.

#### Scenario: Oversized compact input keeps the Lite prelude

- **WHEN** compact input trimming is required for a Responses Lite request
- **THEN** every required `additional_tools` item remains in the upstream input
- **AND** typed and role-only system/developer state remains in the upstream input

#### Scenario: Compact input keeps a latest tool pair that fits

- **WHEN** compact trimming is required, the latest input item is a non-state, non-side-effecting tool call or tool output, and its complete pair fits with required anchors and trim markers
- **THEN** the latest item remains in the upstream input
- **AND** any matching call or output present in the supplied input is retained with it

#### Scenario: Oversized non-state tool tail leaves room for trim markers

- **WHEN** the latest input item is a non-state, non-side-effecting tool call or output whose complete pair cannot fit with required anchors and trim markers
- **THEN** the service omits the call and output together and represents the omission with a compact-trim marker
- **AND** it does not return `responses_compact_input_too_large` solely because the pair fit before marker framing
- **AND** the marker does not claim omitted terminal context was preserved

#### Scenario: Continuity-anchored latest tool output remains required

- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest input item is a tool output without a matching call in the supplied input
- **THEN** the output remains in the upstream input because its call belongs to the prior response
- **AND** the service returns `responses_compact_input_too_large` when that required output cannot fit

#### Scenario: Ordinary non-patch paired tail may be omitted

- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest ordinary, non-`apply_patch` tool output has a matching call in supplied input
- **THEN** compact trimming MAY omit the complete pair when it cannot fit
- **AND** this allowance does not apply to an `apply_patch` call or output

#### Scenario: Reused call ID from another tool variant does not satisfy continuity

- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest tool
  output reuses the `call_id` of an incompatible function/custom/apply-patch
  call variant in supplied input
- **THEN** the latest output remains required as continuity from the previous response
- **AND** the incompatible supplied call is not retained as its pair

#### Scenario: Oversized latest unmatched tool call fails closed

- **WHEN** the latest compact input item is an unmatched tool call that cannot fit the compact wire budget
- **THEN** the service returns `responses_compact_input_too_large` rather than representing the call with a compact-trim marker

#### Scenario: Side-effecting tail remains required

- **WHEN** the latest compact input item is an `apply_patch_call`, `apply_patch_call_output`, or a tool call or matching pair classified as side-effecting by the canonical tool-safety classifier
- **THEN** the item and any matching counterpart remain required compact context
- **AND** the service returns `responses_compact_input_too_large` rather than omitting the side-effecting patch record when they cannot fit

#### Scenario: Reused call IDs keep only the required occurrence

- **WHEN** an older tool call and a required state-tool call reuse the same call ID
- **THEN** compact trimming retains the output matched to the required state-call occurrence
- **AND** it does not retain an oversized historical output solely because its earlier call reused that ID

#### Scenario: Exact-budget backtracking drops an optional tool pair together

- **WHEN** optional tool context fits the approximate item budget but trim-marker framing exceeds the exact wire cap
- **THEN** backtracking removes the optional call and its matching output as one group
- **AND** it does not re-add either counterpart while preserving every required item

### Requirement: Compact trimming preserves prioritised historical side effects

The service MUST retain recognised historical side-effect tool calls as bounded
priority context when an oversized compact input is trimmed. It MUST use the
same side-effect classifier as downstream replay
deduplication. This includes code-mode `exec` and `collaboration` wrapper calls
as well as their lower-level tool spellings and recognised parallel batches.

For each retained historical side effect, compact trimming MUST retain its
matching call and output together. The service MUST reserve space for that
complete pair before selecting optional ordinary head or tail context. Required
state anchors and the current required item remain mandatory; if they leave no
room for a historical pair, the service MAY drop that pair together and retain a
trim marker instead.

A recognised side-effect call without a non-empty `call_id` MUST NOT be
retained as a historical side-effect anchor, because it cannot form a verified
call/output pair.

#### Scenario: Code-mode side effect survives an oversized compact input

- **WHEN** an oversized compact input contains a historical custom `exec` or
  `collaboration` call with its matching output outside required state context
- **THEN** the trimmed upstream input retains both the call and its output when
  the pair fits with required state
- **AND** optional ordinary tail context is dropped before that pair

#### Scenario: Historical side-effect pair cannot fit with required state

- **WHEN** required state anchors and the current required item leave no room
  for a historical side-effect call and its matching output
- **THEN** compact trimming drops the entire historical pair
- **AND** it does not retain only one member of that pair

#### Scenario: Side-effect call lacks a usable pair key

- **WHEN** an oversized compact input contains a recognised historical
  side-effect call without a non-empty `call_id`
- **THEN** compact trimming does not preserve that call as a side-effect anchor
- **AND** it does not emit an unpaired historical side-effect call upstream

#### Scenario: Final compact wire expansion is rejected locally

- **WHEN** Unicode escaping, JSON array framing, or image inlining makes the final compact input exceed the upstream limit
- **THEN** the service returns `responses_compact_input_too_large` before an upstream attempt
- **AND** any API-key reservation is released
- **AND** no upstream account is penalized

#### Scenario: Terminal compaction trigger validates before admission

- **WHEN** a streaming Responses request ends with `compaction_trigger` and its derived compact input cannot fit
- **THEN** the service returns the same invalid-client-payload response before admission, reservation, account selection, or upstream compact work

#### Scenario: Enforced non-Lite model rejects Lite input

- **WHEN** API-key policy rewrites Lite-shaped input to a model whose catalog metadata disables Responses Lite
- **THEN** the service rejects the request before any upstream HTTP or websocket attempt

#### Scenario: Replayed code-mode side effects are emitted once

- **WHEN** reconnect replay repeats the same code-mode `exec` or `collaboration` call identity
- **THEN** the downstream client receives that side-effecting call only once

#### Scenario: Distinct code-mode calls remain distinct

- **WHEN** request history has different call IDs with identical code-mode source text and matching outputs
- **THEN** every call and matching output remains in the forwarded history

### Requirement: Reasoning summaries omit blank HTML comment placeholders

Responses reasoning output items and summary delta/part events MUST remove standalone blank HTML comment placeholder lines from `summary_text` before forwarding them to clients, including markers split across delta boundaries. This cleanup applies to both `/backend-api/codex/responses` and `/v1/responses` streamed or collected output item paths. The cleanup MUST be limited to reasoning summary text and MUST NOT rewrite placeholder-free whitespace, assistant-visible message content, inline blank comments, or non-empty HTML comments.

#### Scenario: Codex CLI route does not expose blank comment marker

- **GIVEN** upstream emits a reasoning output item with `summary: [{"type":"summary_text","text":"**Planning**\n\n<!-- -->"}]`
- **WHEN** a Codex CLI client streams `POST /backend-api/codex/responses`
- **THEN** the forwarded reasoning summary text is `**Planning**`
- **AND** the stream does not contain `<!-- -->`

### Requirement: HTTP bridge admission waiters survive upstream replacement

The proxy MUST preserve an HTTP bridge session when its upstream connection
terminates while an unsent request is already waiting for that session's
response-create admission. It MUST fail the requests that were pending on the
terminated upstream but MUST NOT retire, unregister, prune, or release the
retained session while the unsent waiter owns the handoff.

After the waiter acquires admission, the proxy MUST reconnect the retained
session before sending the request. A waiter that has not entered the pending
request queue and has no upstream send timestamp MAY be sent exactly once on
that fresh connection. Hard-affinity sessions MUST retain their account and
continuity ownership during this handoff. If the session was replaced or
unregistered, or reconnection fails, the proxy MUST fail closed without sending
the waiter. Cancelling or failing the last waiter MUST allow the closed session
to retire and release its resources.

#### Scenario: admitted follow-up survives an upstream close

- **GIVEN** one HTTP bridge request is pending upstream
- **AND** a follow-up request is unsent and waiting on the same response-create gate
- **WHEN** the upstream connection closes before the follow-up acquires the gate
- **THEN** the pending request receives its terminal continuity failure
- **AND** the session remains registered and protected from pruning for the waiter
- **AND** the waiter reconnects the retained session and is sent exactly once
- **AND** the waiter does not receive an internal bridge-closed error

#### Scenario: unsafe handoff fails closed

- **GIVEN** an unsent waiter whose prior session was replaced or unregistered
- **OR** the retained session cannot reconnect
- **WHEN** the waiter acquires admission
- **THEN** the waiter is not sent
- **AND** the request receives an explicit retryable proxy error

### Requirement: Selected Codex installation identity is internally consistent

For native Codex requests, the service MUST use an account-specific installation id consistently.
When that id is applied, the service MUST use the same id in `x-codex-installation-id` and in
an existing `x-codex-turn-metadata.installation_id` field on every upstream
Responses transport. Missing, malformed, or non-object turn metadata MUST be
preserved rather than invented or discarded.

#### Scenario: Both canonical metadata carriers are present in a payload

- **WHEN** a native Responses payload contains both installation metadata
  carriers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Both canonical metadata carriers are present in headers

- **WHEN** a native HTTP or WebSocket request carries both installation
  metadata headers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Turn metadata cannot be safely rewritten

- **WHEN** `x-codex-turn-metadata` is malformed JSON, is not a JSON object, or
  does not contain `installation_id`
- **THEN** the service preserves that turn metadata unchanged
- **AND** it still applies the selected account id through the standalone
  installation-id carrier

### Requirement: Safe HTTP bridge pre-created retries MUST avoid stalled owners

When an unanchored HTTP bridge request is retried before visible output, the service MUST exclude the account that failed to create the response when the
request has no account-scoped file requirement. A request with an account-
scoped file requirement MUST remain bound to its file owner.

#### Scenario: unanchored bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request is safely replayable before
  `response.created`
- **AND** it has no account-scoped file requirement
- **THEN** the bridge excludes the stalled account before reconnecting

#### Scenario: file-backed bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request requires its file-owner account
- **AND** it is retried before `response.created`
- **THEN** the bridge does not exclude or clear the required file owner

### Requirement: Direct capacity-wait progress follows the downstream stream contract

When direct HTTP/SSE streaming waits for recoverable local account capacity, the proxy MUST emit `codex.keepalive` progress events if the OpenAI SDK stream contract is disabled, regardless of whether the route propagates HTTP errors.
The proxy MUST continue suppressing those non-standard progress events before
startup when both HTTP error propagation and the OpenAI SDK stream contract are
enabled.

#### Scenario: Native image-capable bypass emits capacity progress

- **GIVEN** an image-capable native Codex request bypasses the HTTP responses bridge
- **AND** the route propagates HTTP errors with `enforce_openai_sdk_contract = false`
- **WHEN** direct account selection waits for `account_stream_cap` or `account_response_create_cap` to recover
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"` before capacity is released
- **AND** no upstream response attempt or terminal event occurs before capacity is released
- **AND** account selection retries and the real upstream completion is forwarded after capacity becomes available

#### Scenario: OpenAI SDK startup error remains structured

- **GIVEN** a route propagates HTTP errors with `enforce_openai_sdk_contract = true`
- **WHEN** a local account-capacity wait occurs before stream startup
- **THEN** the proxy MUST NOT emit `codex.keepalive` before startup
- **AND** a terminal local-cap failure remains available to the route's structured HTTP error path

### Requirement: Direct WebSocket replay never mixes numeric response sequences

For direct Responses WebSocket requests, the proxy MUST NOT transparently replay a request on a fresh upstream generation after any finite integer `sequence_number` frame for that request has been successfully sent downstream. When an upstream close would otherwise trigger replay, the proxy MUST settle the failed pending request without emitting frames from a new upstream generation under the existing downstream response id, and MUST close the downstream WebSocket with code 1011 so the client can retry on a fresh transport. When an upstream terminal error would otherwise trigger quota, authentication, security-work, or equivalent replay, the proxy MUST finalize and surface that terminal error without reconnecting. Suppressed frames and non-integer sequence sentinels MUST NOT by themselves disable otherwise-safe replay.

#### Scenario: Sequenced response is interrupted before completion

- **WHEN** a direct WebSocket request has emitted `response.created` or another frame with a finite integer `sequence_number`
- **AND** upstream closes before a terminal response event
- **THEN** codex-lb does not transparently replay that request under the existing downstream response id
- **AND** no lower replay sequence is emitted downstream
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Unsafe replay settles request ownership

- **WHEN** sequenced replay is refused after upstream close
- **THEN** response-create admission, account-local leases, API-key reservations, and request logging are finalized exactly once
- **AND** the failed attempt does not become a successful continuity owner

#### Scenario: Sequenced retryable terminal event is not replayed

- **WHEN** a direct WebSocket request has successfully emitted a finite integer `sequence_number`
- **AND** upstream emits a terminal error that would ordinarily trigger transparent quota, authentication, or security-work replay
- **THEN** codex-lb does not reconnect or resend the request
- **AND** the terminal error is finalized and remains client-visible under the existing error contract

#### Scenario: Sequence-free startup remains replayable

- **WHEN** upstream closes before any numeric sequence-bearing frame has been successfully sent downstream
- **AND** the request otherwise satisfies the existing one-shot replay guard
- **THEN** codex-lb MAY transparently replay the request on a fresh upstream connection

#### Scenario: Suppressed frame does not establish exposure

- **WHEN** codex-lb suppresses an upstream frame before downstream emission
- **AND** the suppressed frame contains a numeric `sequence_number`
- **THEN** that frame does not establish the downstream sequence watermark

### Requirement: Downstream websocket ingress accepts large response.create messages
The server MUST accept client-to-proxy websocket messages on the Responses websocket routes (`/backend-api/codex/responses`, `/v1/responses`) up to a configurable ingress budget before closing the connection at the protocol layer. The default budget MUST be 128 MiB, matching the HTTP responses-path decompressed body cap. The budget MUST be configurable via the `--ws-max-size` CLI flag and the `UVICORN_WS_MAX_SIZE` environment variable, with the CLI flag taking precedence. The server MUST continue to negotiate `permessage-deflate` on the client-facing websocket, and the ingress budget MUST apply to the decompressed message size.

#### Scenario: Oversized response.create reaches the application-level guard
- **WHEN** a client sends a single websocket text message larger than 16 MiB but within the configured ingress budget
- **THEN** the server delivers the message to the application layer instead of closing the connection with `1009 message too big`
- **AND** the application-level oversized-`response.create` handling (historical slimming, then local rejection) applies

#### Scenario: Operator overrides the ingress budget
- **WHEN** the operator starts the server with `--ws-max-size <bytes>` or sets `UVICORN_WS_MAX_SIZE=<bytes>`
- **THEN** the websocket ingress message budget uses the configured value
- **AND** an invalid (non-positive or non-integer) value fails startup with a clear error

### Requirement: Oversized response.create payloads are slimmed or rejected fail-fast before upstream send
When the service prepares a Responses `response.create` request for the upstream websocket, it MUST measure the serialized outbound request size before sending it upstream. If the payload exceeds the upstream websocket budget, the service MUST first attempt to slim only the historical portion of `input` that precedes the most recent user turn: historical inline images MUST be replaced with textual omission notices, and oversized historical tool outputs MUST be replaced with textual omission notices that preserve the item in sequence. If the request still exceeds budget after slimming, the service MUST fail locally with status `400` — not `413` — carrying `error.code = "payload_too_large"`, `error.type = "invalid_request_error"`, and `error.param = "input"`, because the official Codex client treats `400` as a non-retryable invalid-request error surfaced immediately while `413` triggers five full-payload retries followed by a sticky session-wide websocket-to-HTTP transport downgrade.

#### Scenario: Historical inline artifacts are slimmed and the latest user turn is preserved
- **WHEN** a Responses request exceeds the upstream websocket budget because historical inline images or historical oversized tool outputs dominate the serialized `input`
- **AND** replacing those historical artifacts with omission notices reduces the serialized request below budget
- **THEN** the service forwards the slimmed `response.create` upstream
- **AND** it preserves the most recent user turn unchanged

#### Scenario: HTTP Responses route fails locally with 400 when the payload still exceeds budget
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service returns HTTP `400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT allocate or reuse an upstream websocket bridge session for that request

#### Scenario: Websocket Responses route fails locally with a status-400 error event when the payload still exceeds budget
- **WHEN** a websocket `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service emits a websocket error event with `"type": "error"` and `"status": 400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT connect the upstream websocket for that request

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, account-capacity recovery waits, and upstream stream attempts. Each upstream stream attempt MUST clamp its connect timeout, idle timeout, and total request timeout to the remaining request budget.

#### Scenario: Remaining budget constrains all stream attempt timeouts
- **WHEN** account selection, account-capacity recovery, or token refresh leaves only part of the request budget available before a stream attempt starts
- **THEN** the proxy limits the upstream connect timeout, SSE idle timeout, and upstream request total timeout to that same remaining budget
- **AND** the client receives `response.failed` with `upstream_request_timeout` once that budget is exhausted instead of waiting through the full configured stream windows

#### Scenario: Forced refresh retry recomputes all attempt timeouts
- **WHEN** a first stream attempt fails with an authentication error that triggers a forced token refresh and retry
- **THEN** the proxy recomputes the remaining request budget after the refresh
- **AND** the retry attempt reapplies connect, idle, and total timeout limits from that recomputed budget

#### Scenario: Recoverable account-capacity wait is bounded by the request budget
- **WHEN** account selection reports a recoverable retry hint such as temporary rate-limit or stream-capacity exhaustion
- **AND** the streaming request still has remaining request budget
- **THEN** the proxy may wait for at most the smaller of the recovery hint and the remaining request budget before retrying selection
- **AND** if the budget is exhausted before an account becomes available, the request fails through the normal no-account or rate-limit error path instead of starting a fresh full-budget wait

#### Scenario: Local balancer rate-limit exhaustion is not treated as recoverable capacity
- **WHEN** account selection reports the local balancer message `Rate limit exceeded. Try again in Ns`
- **AND** the selection result is a local no-account failure with `no_accounts` or no explicit error code
- **THEN** the proxy does not enter an account-capacity recovery wait from that local retry hint
- **AND** the request returns through the normal no-account or rate-limit error path instead of repeatedly retrying the same local selection failure

#### Scenario: Local account cap selection waits instead of failing immediately
- **WHEN** account selection for a streaming Responses request fails locally with `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy treats the condition as a recoverable account-capacity wait within the request budget
- **AND** it retries account selection after the bounded wait instead of returning an immediate 429
- **AND** permanent `no_accounts` failures remain non-waitable unless they carry a distinct recoverable capacity or upstream quota signal

#### Scenario: Post-selection response-create capacity preserves routing invariants
- **WHEN** a selected account reaches `account_response_create_cap` before downstream output is visible
- **THEN** an unpinned request MUST prefer an eligible alternate account before waiting
- **AND** an owner-bound, file-pinned, or otherwise same-account retry MUST keep or reacquire its stream lease while waiting within the original request budget
- **AND** the same behavior applies after a forced token refresh

#### Scenario: SDK-contract propagated startup errors remain observable
- **WHEN** a route requests HTTP error propagation, enforces the OpenAI SDK stream contract, and waits for local account capacity before startup
- **THEN** the route MUST perform the bounded recovery wait instead of raising the first cap error immediately
- **AND** it MUST NOT emit an account-capacity keepalive before startup succeeds, so a terminal startup error can still use the route's structured error path

#### Scenario: Existing HTTP bridge session waits on submit capacity
- **WHEN** HTTP bridge session submission reaches `account_response_create_cap`
- **THEN** a hard-affinity or file-pinned request MUST wait and retry submission within the bridge request budget
- **AND** a soft-affinity request MUST retain its existing alternate-session reroute behavior before waiting on the saturated session

#### Scenario: WebSocket account selection waits on local caps
- **WHEN** downstream WebSocket account selection returns `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy MUST emit a `codex.keepalive` with status `waiting_for_account_capacity`
- **AND** retry selection within the original WebSocket request budget
- **AND** return the original local-cap error if that budget is already exhausted

### Requirement: Streaming account-capacity waits keep clients alive
When a streaming Responses request waits for temporary account capacity to recover before account selection can continue, the proxy MUST emit downstream progress events during the wait. HTTP/SSE and HTTP bridge streams MUST emit `codex.keepalive` events with `status = "waiting_for_account_capacity"`, request id, elapsed wait seconds, and retry-after seconds when known. HTTP bridge streams MAY also emit `response.in_progress` to satisfy OpenAI Responses stream parsers before later terminal events. WebSocket clients MUST receive equivalent `codex.keepalive` JSON messages. These progress events MUST NOT expose account emails, API keys, raw affinity keys, prompt content, or request payloads. Contract-shaped streams remain subject to the direct capacity-wait progress requirement, which suppresses non-standard progress events before startup when both HTTP error propagation and the OpenAI SDK stream contract are enabled.

#### Scenario: HTTP/SSE capacity wait emits keepalive
- **WHEN** `/v1/responses` streaming account selection can recover after a retry hint
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"`
- **AND** includes the request id, waited seconds, and bounded retry-after seconds

#### Scenario: HTTP bridge capacity wait preserves parser progress
- **WHEN** an HTTP responses bridge request waits for session creation or account selection capacity
- **THEN** the bridge stream emits a capacity-wait keepalive
- **AND** emits OpenAI-compatible in-progress events when needed so downstream Responses stream parsers do not time out before the terminal response

#### Scenario: WebSocket capacity wait emits JSON keepalive
- **WHEN** a WebSocket Responses request waits for account capacity recovery
- **THEN** the downstream WebSocket receives a JSON `codex.keepalive` message with `status = "waiting_for_account_capacity"`
- **AND** the connection remains open until selection retries, the request budget expires, or the client disconnects

### Requirement: Downstream-HTTP upstream transport follows a configurable policy

When a downstream HTTP/SSE request (`request_transport == "http"`) resolves its base upstream transport to `"websocket"`, the proxy MUST decide the final upstream transport using the configured `http_downstream_transport_policy`, after all higher-precedence rails have been applied, and the policy MUST NOT affect native WebSocket clients (`request_transport == "websocket"`), which keep their dedicated upstream WebSocket path.

Precedence (highest first), evaluated before the policy:

1. An explicit `upstream_stream_transport` override of `"http"` or
   `"websocket"` wins outright.
2. Oversized-payload bypass and image / image-generation bypass force
   upstream HTTP.
3. The effective policy (per-API-key `transport_policy_override` when
   set, otherwise the global `http_downstream_transport_policy`) decides.

Policy values and behavior:

- `always_http` (and its alias `pinned`): the request MUST be sent over
  upstream HTTP `POST`, preserving the legacy unconditional pin.
- `always_websocket`: the request MUST keep upstream WebSocket whenever
  the base transport resolved to `"websocket"` without replacing a base
  `"auto"` transport mode with a hard `"websocket"` override.
- `smart` (default): the request MUST keep upstream WebSocket **iff** at
  least one sticky-continuation signal is present on the request, and
  MUST otherwise fall back to upstream HTTP. The sticky-continuation
  signals are:
  - a non-null `previous_response_id` on the request payload, **OR**
  - a `prompt_cache_key` present on the request model, **OR**
  - a Codex session header (`session_id`, `x-codex-session-id`, or
    `x-codex-conversation-id`), **OR**
  - an `x-codex-turn-state` continuity header.

When a policy decision keeps upstream WebSocket, the proxy MUST preserve
the configured/base downstream transport mode passed to the upstream
client. In particular, a base `"auto"` mode MUST remain `"auto"` so the
existing WebSocket-handshake rejection fallback to upstream HTTP remains
available. The policy MAY force a concrete transport override only when
the decision is to downgrade to upstream HTTP.

The per-API-key `transport_policy_override`, when non-null, MUST be used
as the effective policy for requests authenticated by that key and MUST
take precedence over the global default. A null override MUST fall
through to the global `http_downstream_transport_policy`.

#### Scenario: single-shot downstream-HTTP request falls back to HTTP under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport resolves to `"websocket"`
- **AND** a downstream HTTP request carries no `previous_response_id`, no
  `prompt_cache_key`, no Codex session header, and no `x-codex-turn-state`
  header
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: sticky downstream-HTTP request keeps WebSocket under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport mode is `"auto"` and resolves to `"websocket"`
- **AND** a downstream HTTP request carries any one of
  `previous_response_id`, `prompt_cache_key`, a Codex session header, or
  an `x-codex-turn-state` header
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`
- **AND** an upstream WebSocket handshake rejection status eligible for
  auto fallback MUST transparently retry over upstream HTTP

#### Scenario: always_http policy preserves the legacy pin

- **GIVEN** `http_downstream_transport_policy` is `"always_http"` (or
  `"pinned"`) and the base upstream transport resolves to `"websocket"`
- **WHEN** a downstream HTTP request resolves the upstream transport,
  regardless of sticky signals
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: always_websocket policy never downgrades sticky-less HTTP

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
  and the base upstream transport mode is `"auto"` and resolves to
  `"websocket"`
- **WHEN** a downstream HTTP request with no sticky signals resolves the
  upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`

#### Scenario: per-key override wins over the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has
  `transport_policy_override = "always_http"`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`,
  because the per-key override takes precedence

#### Scenario: null per-key override follows the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has `transport_policy_override =
  null`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket, following the global
  `smart` policy

#### Scenario: explicit websocket override still beats the policy

- **GIVEN** `upstream_stream_transport` is explicitly `"websocket"`
- **WHEN** a single-shot downstream HTTP request with no sticky signals
  resolves the upstream transport under any policy
- **THEN** the explicit override MUST win and the request MUST use
  upstream WebSocket

#### Scenario: oversized payload bypass still forces HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** the serialized request payload exceeds the WebSocket frame
  budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  oversized-payload bypass has higher precedence than the policy

#### Scenario: native WebSocket clients are unaffected by the policy

- **GIVEN** any value of `http_downstream_transport_policy`
- **WHEN** a native WebSocket client (`request_transport == "websocket"`)
  streams a request
- **THEN** the client MUST keep its dedicated upstream WebSocket path and
  the policy MUST NOT downgrade it to HTTP

### Requirement: Request-scoped Codex metadata survives HTTP-to-WebSocket bridging

When an HTTP Responses request is translated into an upstream WebSocket `response.create` frame, the service MUST project nonblank `x-codex-turn-metadata`, `x-openai-subagent`, `x-codex-parent-thread-id`, and `x-codex-window-id` compatibility headers into that frame's `client_metadata`. This projection MUST happen for every request, including requests multiplexed over a reused upstream socket. A metadata value already supplied in the request body MUST remain authoritative over the compatibility header, and header matching MUST be case-insensitive.

#### Scenario: Reused bridge session receives a subagent turn

- **GIVEN** a parent HTTP request has opened an upstream Responses WebSocket
- **WHEN** a subagent HTTP request reuses that socket with subagent, parent-thread, and child-window headers
- **THEN** the subagent request's `response.create.client_metadata` contains those values
- **AND** the earlier parent frame retains its own window metadata
- **AND** no value is inherited solely from the socket handshake

#### Scenario: Body metadata remains canonical

- **WHEN** a request body and compatibility header provide different values for the same Codex metadata key
- **THEN** the upstream `response.create.client_metadata` retains the body value

### Requirement: Compact routing honors turn-state affinity

When a compact request carries a nonblank `x-codex-turn-state`, the service MUST classify that value as Codex-session affinity before considering a session header, prompt-cache affinity, or sticky-thread affinity. This precedence MUST apply even when generic Codex session-header affinity is disabled, matching the normal Responses path.

#### Scenario: Turn-state-only compact remains on the turn owner

- **GIVEN** a Responses turn established an account mapping for an `x-codex-turn-state` value
- **AND** another account becomes preferable under the non-sticky routing strategy
- **WHEN** `/responses/compact` carries only that turn-state continuity value
- **THEN** the compact request is routed to the account that owns the turn-state mapping

#### Scenario: Turn-state overrides less-specific affinity

- **WHEN** a compact request carries turn-state, session-header, and prompt-cache keys
- **THEN** its affinity key is the turn-state value
- **AND** its affinity kind is Codex session

### Requirement: Namespaced side-effect replay dedupe preserves call identity

For a namespaced side-effect function or custom-tool call, the service MUST use the call's namespace and call ID as part of downstream and replayed-history deduplication identity. An exact replay with the same namespace, name, call ID, and canonical arguments MUST remain suppressed. Calls with different namespaces or different nonblank call IDs MUST remain distinct, even when their names and canonical arguments match, and their matching outputs MUST remain in forwarded history.

Flat legacy side-effect calls MAY continue to use argument-based replay identity so reconnects that change only a call ID do not repeat shell, patch, or terminal side effects.

#### Scenario: Distinct namespaced spawns use identical arguments

- **WHEN** two `collaboration.spawn_agent` calls have identical arguments and different call IDs
- **THEN** both calls are forwarded
- **AND** both matching outputs remain in replayed request history

#### Scenario: Exact namespaced call is replayed after reconnect

- **WHEN** reconnect replay emits the same namespaced call ID and canonical arguments under a new response ID
- **THEN** the service suppresses the replayed downstream call

#### Scenario: Equal call identity appears in different namespaces

- **WHEN** two side-effect calls share a name, call ID, and arguments but have different namespaces
- **THEN** the service treats them as distinct calls

### Requirement: Compact requests preserve scoped turn-state ownership

When a compact request contains a real client-supplied `x-codex-turn-state`, the system MUST resolve the token only in the requesting API key scope and select only that owner account. If the owner cannot be resolved or selected, the request MUST fail closed and MUST NOT fall back to a generic sticky or load-balanced account. Proxy-synthesized first-turn placeholders (the `turn_*` / `http_turn_*` values codex-lb injects when the client did not supply one) are not real continuity tokens until registered as bridge aliases; an unregistered placeholder MUST NOT block file-owner routing, but a registered placeholder MUST still resolve to its owner account.

#### Scenario: Token belongs to the requesting API key

- **GIVEN** an active turn-state owner exists for the requesting API key
- **WHEN** the client submits a compact request with that token
- **THEN** compact selection is constrained to that owner account

#### Scenario: Unscoped sticky state cannot supply a turn-state owner

- **GIVEN** a turn-state token has no owner in the requesting API-key-scoped local or durable bridge indexes
- **WHEN** an unscoped sticky-session mapping exists for the same token
- **THEN** compact owner resolution fails closed
- **AND** the unscoped sticky-session mapping is not consulted

#### Scenario: Token belongs to a different API key or is unavailable

- **GIVEN** the token has no owner in the requesting API key scope
- **WHEN** the client submits a compact request with that token
- **THEN** the request fails with `turn_state_owner_unavailable`
- **AND** no generic account is selected

#### Scenario: Registered synthesized placeholder belongs to the requesting API key

- **GIVEN** a proxy-synthesized `http_turn_*` token has been registered as a bridge alias
- **WHEN** the client later submits a compact request with that token
- **THEN** compact selection is constrained to the registered owner account

#### Scenario: Synthesized first-turn placeholder does not override file-owner routing

- **GIVEN** the request carries only a proxy-synthesized `x-codex-turn-state`
- **AND** the payload references an `input_file.file_id` pinned to an account
- **WHEN** the client submits the compact request
- **THEN** compact routing may use the pinned file owner
- **AND** the synthesized placeholder does not trigger `turn_state_owner_unavailable`

### Requirement: Collected failures retain upstream turn-state metadata

The system MUST copy a real `x-codex-turn-state` received in a `response.metadata` event into the HTTP headers of a collected response, including when the later terminal event is `response.failed`.

#### Scenario: Metadata precedes a failed response

- **GIVEN** a collected response stream emits turn-state metadata
- **AND** the terminal response is failed
- **THEN** the returned HTTP error includes the captured turn-state header

### Requirement: WebSocket incomplete responses preserve the upstream reason in request logs

When an upstream Responses WebSocket terminal `response.incomplete` event contains a non-empty string at `response.incomplete_details.reason`, the service SHALL persist the request log with status `error` and SHALL preserve that reason as both `error_code` and `error_message`. The terminal event sent to the downstream client and the account-health treatment of an incomplete response SHALL remain unchanged.

#### Scenario: max-output limit is identifiable in a WebSocket request log

- **WHEN** the upstream emits `response.incomplete` with
  `incomplete_details.reason` equal to `max_output_tokens`
- **THEN** the corresponding WebSocket request log has status `error`,
  `error_code` equal to `max_output_tokens`, and `error_message` equal to
  `max_output_tokens`
- **AND** the account is not marked unhealthy solely because of that
  incomplete event

### Requirement: OpenAI-compatible sources route only compatible public routes

OpenAI-compatible model sources SHALL be eligible for public OpenAI-compatible
routes only when the source declares support for the route shape. Chat
Completions-compatible sources MAY serve `/v1/chat/completions`.
Responses-compatible sources MAY serve `/v1/responses` and
`/backend-api/codex/responses`. Audio-transcriptions-compatible sources MAY
serve `/v1/audio/transcriptions`. Codex-native compaction, file upload,
control-plane, and websocket bridge paths MUST remain subscription-backed unless
a later requirement explicitly defines OpenAI-compatible source behavior for
those paths.

#### Scenario: Chat completions routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares chat-completions support
- **AND** the authenticated API key is allowed to use that source/model
- **WHEN** the client calls `POST /v1/chat/completions` with that model
- **THEN** the proxy forwards the request to the source's configured base URL
  using the source's upstream API key

#### Scenario: Codex-native Responses route uses Responses-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares Responses support
- **AND** it exposes model `deepseek-v4-flash`
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `deepseek-v4-flash`
- **THEN** the proxy forwards the request to that source's Responses endpoint

#### Scenario: Chat-only source is not used for Codex-native Responses route

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** the source declares Chat Completions support only
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `local-coder`
- **THEN** the request is not routed to that source
- **AND** subscription-backed Codex routing rules continue to apply

#### Scenario: Compaction request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /backend-api/codex/responses` for that model whose
  input contains a `compaction_trigger` item
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription-backed Codex compaction path instead

#### Scenario: V1 compaction_trigger remains eligible for model sources

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /v1/responses` for that model whose input ends with
  a terminal `compaction_trigger` item
- **THEN** the request remains eligible for that Responses-compatible source
- **AND** it is not forced onto subscription account selection by the Codex-only
  compaction source-route exclusion

#### Scenario: File-referencing request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `/backend-api/codex/responses` or `/v1/responses` for that
  model whose input references an uploaded `input_file`/`input_image` `file_id`
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription path so the account-scoped file pin is honored

#### Scenario: Audio transcription routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares audio transcriptions support
- **AND** it exposes model `whisper-large-v3`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with multipart
  field `model=whisper-large-v3`
- **THEN** the proxy forwards the multipart request to the source's
  `/audio/transcriptions` endpoint
- **AND** the request uses the source's upstream API key

#### Scenario: Non-source transcription model keeps subscription validation

- **GIVEN** no audio-transcriptions-compatible source exposes model `gpt-4o-mini`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with
  `model=gpt-4o-mini`
- **THEN** the proxy returns the existing unsupported transcription model error

### Requirement: Previous-response source routing follows proven ownership

When a Responses request targets a configured Responses-compatible model source and carries `previous_response_id`, the proxy MUST use recorded subscription-account ownership as the veto for model-source routing. The proxy MUST NOT infer ownership from the response identifier's syntax. A recorded subscription owner MUST keep the request on subscription routing. When no subscription owner is recorded, the configured model source MUST remain authoritative, including when the identifier uses the canonical OpenAI `resp_` hexadecimal shape.

For the direct Responses WebSocket transport, a recorded subscription owner MUST keep the request on the owner-bound subscription path. A configured source model without a recorded subscription owner MUST retain the existing `model_source_requires_http_transport` fallback behavior.

#### Scenario: Recorded subscription owner overrides an HTTP model source

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is not forwarded to the model source
- **AND** subscription routing preserves the recorded account owner

#### Scenario: Canonical source response ID remains source-routed over HTTP

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is forwarded to the configured model source

#### Scenario: Direct WebSocket preserves a recorded subscription owner

- **GIVEN** a source is also configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the request remains on the owner-bound subscription WebSocket path
- **AND** the proxy does not emit `model_source_requires_http_transport`

#### Scenario: Direct WebSocket source continuation falls back to HTTP

- **GIVEN** a source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits `model_source_requires_http_transport`
- **AND** the request is not sent to a subscription upstream

### Requirement: Source-routed chat payloads are sanitized before forwarding

Source-routed `/v1/chat/completions` requests SHALL forward the client's
OpenAI-compatible payload with the following sanitization applied to the
outbound body:

- An empty `tools` array MUST be omitted, together with `tool_choice` and
  `parallel_tool_calls`, so tool-less requests reach the source without
  tool-calling artifacts.
- Non-standard reasoning toggles (`include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, and `reasoning_effort`) MUST be stripped
  unless the source model's catalog entry opts into reasoning via
  `raw_metadata_json` containing `"supports_reasoning": true`.
- An API key's enforced reasoning effort MAY still be applied after
  sanitization; explicit operator policy overrides the default strip.

#### Scenario: Empty tools array is not forwarded

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **WHEN** a client calls `POST /v1/chat/completions` for that model without
  tools (or with `"tools": []`) and `"tool_choice": "none"`
- **THEN** the body forwarded to the source contains no `tools`, `tool_choice`,
  or `parallel_tool_calls` keys

#### Scenario: Reasoning toggles are stripped for non-reasoning source models

- **GIVEN** a source model whose catalog entry does not declare
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, or `reasoning_effort` in the request
- **THEN** none of those keys appear in the body forwarded to the source

#### Scenario: Catalog opt-in preserves reasoning toggles

- **GIVEN** a source model whose `raw_metadata_json` contains
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning: true`
- **THEN** the forwarded body preserves the client's reasoning fields

### Requirement: Source-routed audio transcriptions preserve OpenAI-compatible multipart semantics

Source-routed `/v1/audio/transcriptions` requests SHALL forward the inbound
audio file and non-file multipart fields to the selected source's
`/audio/transcriptions` endpoint. The proxy MUST use the stored source API key
for upstream authorization and MUST NOT forward the downstream client's
authorization credential. JSON and non-JSON successful upstream response bodies
SHALL be returned to the client with the upstream content type when present.

#### Scenario: Text transcription response passes through

- **GIVEN** an enabled OpenAI-compatible source exposes model `whisper-large-v3`
- **AND** the client requests `response_format=text`
- **WHEN** the source returns a plain text response
- **THEN** the proxy returns that response body without requiring JSON parsing

#### Scenario: Limited key requires token usage

- **GIVEN** an API key has token or cost limits
- **AND** a source-routed audio transcription response has no token-compatible
  usage fields
- **AND** the source model declares no per-minute audio rate
- **WHEN** the upstream source returns a successful transcription response
- **THEN** the proxy releases the reservation
- **AND** returns `usage_unavailable` instead of allowing unaccounted limited-key usage

### Requirement: Audio transcription sources MAY bill by duration

The proxy SHALL support per-minute audio billing for source models that
declare an `audio_per_minute` rate. When the rate is set and a source-routed
`/v1/audio/transcriptions` response carries a positive audio duration
(top-level `duration` seconds, or a `usage.seconds`/`usage.duration` fallback),
the proxy MUST settle cost as `duration_minutes * audio_per_minute` with zero
tokens, and MUST record that cost on the request log and against the API key's
`cost_usd` limit. Duration billing MUST take precedence over token pricing on
the transcription route. A model with no `audio_per_minute` rate MUST fall back
to token-usage settlement.

#### Scenario: Duration-priced model settles cost from audio length

- **GIVEN** an audio-transcriptions source model with `audio_per_minute = 0.30`
- **AND** an API key with a `cost_usd` limit
- **WHEN** a transcription response reports `duration = 120` seconds and no token usage
- **THEN** the API-key reservation is finalized with 0 tokens and $0.60 cost
- **AND** the request log records `cost_usd = 0.60`

#### Scenario: Duration billing does not require token usage for limited keys

- **GIVEN** an audio-transcriptions source model with an `audio_per_minute` rate
- **AND** an API key with token or cost limits
- **WHEN** a transcription response carries a positive duration but no token usage
- **THEN** the request succeeds and settles from duration
- **AND** the proxy does not return `usage_unavailable`

### Requirement: Upstream Responses payloads omit client-omitted request fields

The service MUST NOT emit top-level request fields the client omitted onto
upstream Responses payloads when the field's absence is meaningful upstream.
In particular, the proxy MUST NOT synthesize a top-level `"tools": []` from
the request model's default for clients that did not send the `tools` field,
on any upstream transport (websocket `response.create` frames, HTTP-bridge
bodies, and direct HTTP stream requests). An explicit client-sent
`"tools": []` MUST be forwarded as `[]`. `tool_choice` and
`parallel_tool_calls` MUST be forwarded only when the client sent them;
an explicit client-sent `parallel_tool_calls: false` MUST reach upstream.
The OpenAI-compatible `/v1/responses` conversion MUST propagate `tools`
omission into the native request so both routes behave identically.
Field omission MUST survive every re-serialization hop: the multi-instance
owner-forward body (internal bridge forward) MUST NOT contain fields the
client omitted, the owner instance receiving a forwarded request MUST NOT
re-mark `tools` as explicitly set, and model-source Responses egress payloads
MUST likewise omit fields the client never sent. The owner forward MUST carry
a v2 signature (`x-codex-bridge-signature-v2`) computed over the same
forwarding serialization that is posted as the body, and the forwarding
origin MUST NOT relay externally supplied `x-codex-bridge-*` headers. The
receiving instance MUST treat the v2 signature as authoritative only when it
validates: a valid v2 signature accepts the forward (proving the received
body was not rewritten, including an injected `"tools": []`); an absent or
invalid v2 header falls back to the legacy signature verification; the
forward is rejected only when neither verifies. Mere v2-header presence MUST
NOT block a legacy-signed forward, because pre-v2 origins relay unknown
inbound bridge headers verbatim and an external client could otherwise deny
legitimate forwards by planting a garbage v2 header. For rolling-upgrade
compatibility the origin MUST also keep sending the legacy signature headers
(computed over the plain dump with the synthesized `"tools": []`) so pre-v2
owners verify unchanged. ROLLOUT SHIM: the legacy header emission and the
legacy fallback are a one-release compatibility shim and MUST be removed in a
follow-up change once fleets are homogeneous on a v2-signing release (grep
for `ROLLOUT SHIM` / `HTTP_BRIDGE_SIGNATURE_V2_HEADER`); while the shim is
active the legacy fallback is exactly as strong as the pre-v2 scheme (a
body-only rewrite injecting `"tools": []` into a dual-signed forward
downgrades to the legacy digest and verifies), and removing the shim restores
strict v2-only rejection.

#### Scenario: Responses Lite request reaches upstream without a tools key

- **WHEN** a `/backend-api/codex/responses` request omits top-level `tools`
  and carries its tool bundle in an `additional_tools` input item
- **THEN** the upstream websocket `response.create` frame contains no
  top-level `tools` key
- **AND** the HTTP-bridge request body contains no top-level `tools` key

#### Scenario: Explicit empty tools array is forwarded

- **WHEN** a client sends `"tools": []` explicitly
- **THEN** the upstream payload contains `"tools": []`

#### Scenario: Unset optional tool fields stay absent

- **WHEN** a client omits `tool_choice` and `parallel_tool_calls`
- **THEN** the upstream payload contains neither field

#### Scenario: Owner-forwarded request keeps tools omitted across instances

- **WHEN** a request that omits top-level `tools` is forwarded to its owner
  instance over the internal HTTP bridge (multi-instance owner forward)
- **THEN** the owner-forward request body contains no top-level `tools` key
- **AND** the owner instance parses the forwarded body without marking
  `tools` as explicitly set, so its upstream payload contains no top-level
  `tools` key
- **AND** the owner-forward signature still verifies on the owner instance

#### Scenario: Owner-forward v2 signature covers the posted body

- **WHEN** an owner-forward body that omitted top-level `tools` is rewritten
  in transit to carry an injected explicit `"tools": []`
- **THEN** the v2 signature verification fails
- **AND** absent a valid legacy shim signature, the owner instance rejects
  the forwarded request with an invalid bridge-forward-signature error
  instead of re-marking `tools` as explicitly set
- **AND** generic body rewrites outside the synthesized-tools equivalence
  class fail both digests and are rejected even while the shim headers are
  present

#### Scenario: Mixed-version fleets keep verifying during a rolling upgrade

- **WHEN** an updated origin forwards a dual-signed tools-less body to an
  owner still running pre-v2 code
- **THEN** the legacy signature header matches the pre-v2 owner's
  recomputation over the plain dump, so the forward verifies unchanged
- **WHEN** a pre-v2 origin forwards a legacy-signed body (no v2 header) to
  an updated owner
- **THEN** the updated owner falls back to legacy verification and accepts
  the forward

#### Scenario: Spoofed v2 header does not deny legacy forwards

- **WHEN** a legacy-signed forward from a pre-v2 origin arrives carrying a
  garbage `x-codex-bridge-signature-v2` header that an external client
  planted (pre-v2 origins relay unknown inbound bridge headers verbatim)
- **THEN** the updated owner treats the invalid v2 signature as
  non-authoritative, falls back to legacy verification, and accepts the
  forward
- **AND** an updated origin strips externally supplied `x-codex-bridge-*`
  headers before forwarding, so its own forwards never relay a planted
  header

#### Scenario: Model-source Responses egress omits unsent tools

- **WHEN** a Responses request that omits top-level `tools` is routed to an
  openai-compatible model source
- **THEN** the payload sent to the model source contains no top-level
  `tools` key

### Requirement: Client tool entries are forwarded byte-preserved

The service MUST forward client-sent top-level `tools` entries to upstream
byte-preserved: the tool array order, per-object key order, unknown keys
(including unknown tool types such as `namespace` entries and non-standard
schema markers), and array-value order (for example `parameters.required`)
MUST reach upstream exactly as the client sent them. Tool canonicalization
(array sorting and recursive key sorting) MUST be used only for prompt-cache
affinity and observability hashing and MUST NOT mutate the outgoing payload.
The affinity/observability hash MUST remain insensitive to tool array order
and object key order.

#### Scenario: Reserved namespace tool survives byte-identical

- **WHEN** a client sends top-level `tools` containing a reserved
  `{"type": "namespace", "name": "collaboration", ...}` entry with nested
  function entries, `strict: false`, unknown property markers, and a
  non-alphabetical `required` array
- **THEN** the upstream `response.create` frame serializes that `tools` array
  byte-identical to the client's serialization

#### Scenario: Affinity hash ignores tool ordering

- **WHEN** two requests differ only in tool array order or tool object key
  order
- **THEN** their tools affinity/observability hash is identical

### Requirement: Streaming events are parsed once and re-serialized only when modified

Within each streaming layer (core client consumer, streaming mixin, bridge upstream reader, /v1 normalizers), an SSE event's JSON payload MUST be parsed at most once and reused by that layer's consumers, and an event that no consumer modified MUST NOT be re-serialized by the /v1 normalizers. Event framing, payload contents, dedupe/rewrite semantics, and error normalization MUST be unchanged.

#### Scenario: Unmodified events pass through the /v1 normalizer verbatim

- **GIVEN** a canonical stream event that no normalizer branch rewrites
- **WHEN** the /v1 response normalizer processes it
- **THEN** the original block is yielded byte-identically without re-serialization

#### Scenario: Tool-call rewrite reuses the parsed event on the no-change path

- **GIVEN** an event without duplicate parallel tool calls
- **WHEN** the rewrite step runs with the caller's parsed event
- **THEN** it returns the original line, payload, and event without re-parsing

#### Scenario: Rewritten events stay consistent

- **WHEN** the rewrite step removes duplicate tool calls
- **THEN** the returned line, payload, and validated event all reflect the rewritten content

### Requirement: Durable bridge ownership distinguishes process incarnations

Durable HTTP bridge ownership MUST include a per-process owner epoch in
addition to the stable bridge instance id and the existing owner fencing epoch.
The process owner epoch MUST be generated when the process starts and MUST be
persisted on newly claimed durable HTTP bridge session rows.

On startup, an instance MUST retire durable HTTP bridge sessions whose
`owner_instance_id` equals the current instance id but whose process owner epoch
is missing or differs from the current process owner epoch. Retired rows MUST
be closed and MUST NOT remain attachable through session-header,
turn-state, previous-response, latest-turn-state, or latest-response lookup.
Retired rows MUST clear stored previous-response, latest-turn-state, input
fingerprint, and pending-tool continuity anchors before any future claim can
reuse the same canonical session key.

#### Scenario: Same-container restart retires previous-process rows

- **GIVEN** a durable HTTP bridge session is ACTIVE under instance
  `container-74e8e7cda9fb` and process epoch `boot-a`
- **WHEN** codex-lb starts again in the same container id with process epoch
  `boot-b`
- **THEN** startup closes the `boot-a` durable session row
- **AND** request-target lookup for that session header, turn state, or
  previous response no longer returns the closed row
- **AND** rows already owned by `boot-b` remain attachable

### Requirement: Dead durable anchors recover transparently when safe

The proxy MUST classify proven-dead durable anchors as automatic recovery
candidates before returning any client-visible error.

When a continuity-bound HTTP bridge request would otherwise return a retryable
`stream_idle_timeout` or cooldown terminal, and the durable lookup that supplied
the request's previous-response anchor is proven dead because its owner
instance, process owner epoch, or lease is no longer current, the proxy MUST
dispatch a fresh turn transparently when the request payload has an existing
safe replay proof, including account-neutral full-context resends and
proxy-injected anchor requests whose captured fresh body is replay-safe. The
client MUST receive the normal upstream stream for that fresh turn and MUST NOT
receive a bridge-specific recovery error.

When the request is bound to a client-provided anchor that cannot be safely
replayed as a fresh turn, the proxy MUST return the same OpenAI-compatible
`previous_response_not_found` error shape and HTTP status used by the existing
previous-response-not-found path. The proxy MUST NOT expose a
`bridge_continuity_recovery_required` code to clients. The proxy MUST keep the
existing retryable `stream_idle_timeout` semantics when the durable owner is
current and the failure is ordinary transient upstream silence.

#### Scenario: Previous-process anchor with replayable context recovers automatically

- **GIVEN** a request is bound to a durable previous-response anchor
- **AND** that durable row belongs to the same instance id but a different
  process owner epoch
- **AND** the payload has a safe full-context replay proof
- **WHEN** the bridge hits the pre-submit, startup-cooldown, or retry-circuit
  idle terminal path
- **THEN** the proxy dispatches the request as a fresh turn without the dead
  previous-response anchor
- **AND** the client receives the normal streaming response
- **AND** the response does not include `stream_idle_timeout` retry guidance or
  a bridge-specific recovery error

#### Scenario: Unreplayable client anchor uses the standard not-found contract

- **GIVEN** a request is bound to a client-provided durable previous-response
  anchor
- **AND** that durable row belongs to a dead owner
- **AND** the payload does not have a safe fresh-turn replay proof
- **WHEN** the bridge must fail closed
- **THEN** the client receives the standard `previous_response_not_found`
  error shape for `previous_response_id`
- **AND** HTTP error collection uses the standard previous-response-not-found
  status
- **AND** the response does not include a bridge-specific recovery code

#### Scenario: Current-owner silence remains retryable

- **GIVEN** a request is bound to a durable owner whose instance id, process
  owner epoch, and lease are current
- **WHEN** upstream produces no response events through the existing idle window
- **THEN** the proxy preserves the existing retryable `stream_idle_timeout`
  behavior

### Requirement: Repeated zero-event idle failures poison dead anchors

For hard HTTP bridge keys, repeated zero-event idle failures MUST use the
existing durable retry-circuit counter to identify an anchor that should no
longer remain addressable. When consecutive failures for the same hard bridge
key reach the configured poison threshold, the proxy MUST abandon durable
continuity for that session and retire the bridge even when admission waiters
exist. The default threshold MUST be no greater than seven failures.

#### Scenario: Admission waiters cannot defer anchor poisoning forever

- **GIVEN** a hard durable bridge key has admission waiters
- **AND** repeated zero-event idle failures for that same key reach the poison
  threshold
- **WHEN** the reader failure path would normally defer retirement for the
  admission waiter
- **THEN** the proxy clears the durable continuity anchors
- **AND** retires the session despite the admission waiter
- **AND** the next attach starts from fresh durable state rather than the
  poisoned previous-response anchor

#### Scenario: Lease liveness comparison is timezone-safe
- **GIVEN** a durable bridge session whose `lease_expires_at` was read from a `timestamptz` column (offset-aware) on PostgreSQL
- **WHEN** the dead-owner classifier evaluates lease liveness against the application's naive-UTC clock
- **THEN** both timestamps MUST be normalized to naive UTC before comparison
- **AND** the anchored-lookup path MUST NOT raise on mixed-awareness datetimes

### Requirement: HTTP bridge model-transition isolation is single-pass

When an HTTP bridge request cannot reuse the session selected by its incoming affinity because that session uses an incompatible model, the service MUST preserve the resulting internal model-parallel key until bridge creation or reuse completes. It MUST NOT reapply the original session-header or turn-state fallback to the same request after selecting that fork.

#### Scenario: Fresh turn state falls back to a session on another model

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** lookup emits at most one model-transition fork for that request scope
- **AND** bridge creation continues under the internal key without closing or reusing the incompatible session

#### Scenario: Follow-up fallback has no previous-response lookup

- **GIVEN** a request carries a fresh generated turn-state header, a `previous_response_id` without a local or durable lookup, and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** the session-header fallback remains an anchored continuation for the rest of that lookup/create operation
- **AND** bridge creation continues under the internal key without a `continuity_lost` error

#### Scenario: Full cache preserves the incompatible parent

- **GIVEN** the HTTP bridge cache is at its session limit and a model transition isolates a session-header fallback into a child key
- **WHEN** creation needs to evict an idle session
- **THEN** the incompatible session-header parent MUST NOT be selected for that eviction
- **AND** ordinary LRU eviction remains eligible for other idle sessions

#### Scenario: In-flight parent completes before model isolation

- **GIVEN** a request waits for an in-flight session-header parent whose completed bridge uses an incompatible model
- **WHEN** the request isolates itself with an internal model-parallel key after that wait
- **THEN** the completed parent MUST receive the same capacity-eviction protection as an immediately available parent

#### Scenario: Compatible session fallback remains reusable

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses a compatible model
- **WHEN** lookup applies the session-header fallback
- **THEN** the compatible bridge remains eligible for normal reuse

### Requirement: Standalone Codex web search is forwarded faithfully

The proxy SHALL expose `POST /backend-api/codex/alpha/search` through the same
proxy-authenticated Codex control-request path used by other unary Codex control
endpoints. The proxy MUST preserve the inbound request body and query parameters,
MUST apply the existing API-key scope, account selection, token refresh, session
affinity, failover, and upstream-route policies, and MUST forward the request to
the upstream `POST /codex/alpha/search` path. Successful downstream responses
MUST preserve the upstream status and body and MUST include only response
headers allowed by the existing Codex control-response policy. Final non-2xx
responses MUST preserve their status while using the existing Codex control
OpenAI error-envelope normalization. The proxy MUST NOT parse, normalize, or
invent a local schema for successful search requests or responses.

#### Scenario: authenticated standalone search reaches the upstream Codex path

- **GIVEN** a valid proxy API key and at least one eligible ChatGPT account
- **WHEN** Codex sends `POST /backend-api/codex/alpha/search` with a JSON body and
  query parameters
- **THEN** the proxy forwards the unchanged body and query parameters to
  `POST /codex/alpha/search` using the selected account credentials
- **AND** the downstream client receives the upstream status and body

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

- **WHEN** a client sends a non-POST request to
  `/backend-api/codex/alpha/search`
- **THEN** the request does not enter the upstream search forwarding path

### Requirement: Pre-acceptance account-model rejections fail over safely

When upstream rejects a Responses request with `invalid_request_error` and the exact message `The '<model>' model is not supported when using Codex with a ChatGPT account.` before accepting the response, the proxy MUST classify the failure internally as `account_model_unsupported`. The quoted model MUST match
the requested model. For native WebSocket, HTTP responses bridge, and raw
HTTP/SSE transports, the proxy MUST make at most one transparent attempt on a
different account that advertises the same model, provided the request can move
without violating continuation or uploaded-file ownership. The proxy MUST
exclude the rejecting account only for that request and MUST NOT record an
account-health penalty for this rejection.

The proxy MUST NOT replay after any response id recognized in an upstream payload,
including a `response.failed` payload that carries `response.id` even when
`response.created` was not observed or an `error` payload with top-level
`response_id`, a nonterminal `response.*`
event, downstream sequence/output, another pending request on the shared
socket, or an earlier replay. If no compatible replacement is available, or
the request is account-bound, the proxy MUST preserve the original upstream
400 error instead of replacing it with `no_accounts`, `stream_incomplete`, or
another proxy-generated failure.

#### Scenario: stale model route retries another advertising account

- **GIVEN** two accounts advertise the requested model in the current routing snapshot
- **AND** upstream rejects the first account with the exact account/model unsupported envelope before `response.created`
- **WHEN** the request has no hard account or uploaded-file binding
- **THEN** the proxy excludes the first account for this request and retries once on the second account
- **AND** it forwards only the replacement attempt's response events downstream
- **AND** it does not penalize the first account's global health

#### Scenario: no replacement preserves the upstream rejection

- **GIVEN** upstream rejects a pre-acceptance request with the exact account/model unsupported envelope
- **AND** no other compatible account is available
- **WHEN** transparent failover cannot select a replacement
- **THEN** the client receives the original HTTP 400 `invalid_request_error`
- **AND** the error is not rewritten to `no_accounts`, `stream_incomplete`, or HTTP 502

#### Scenario: selected replacement failure is surfaced

- **GIVEN** upstream rejects a pre-acceptance request with the exact account/model unsupported envelope
- **AND** the proxy selects a different compatible replacement account
- **WHEN** that replacement attempt fails before acceptance
- **THEN** the client receives the replacement attempt's failure
- **AND** the skipped account's original HTTP 400 is not used as a fallback
- **AND** the proxy does not select a third account after a retryable replacement
  refresh, transport, or server failure

#### Scenario: failed bridge replacement retires without restoring rejected metadata

- **GIVEN** an HTTP responses bridge reconnect has selected and installed a
  replacement account after an account/model rejection
- **WHEN** replacement response-create lease acquisition or request send fails
- **THEN** the proxy forwards the replacement failure and retires that bridge
  session after draining the rejected request
- **AND** it does not restore the rejected account's turn state or headers onto
  the replacement socket

#### Scenario: accepted or visible request is never replayed

- **WHEN** the account/model unsupported envelope arrives after a response id, a nonterminal response event, downstream sequence/output, or an earlier replay
- **THEN** the proxy does not transparently replay the request on another account

#### Scenario: account-bound request is never migrated

- **WHEN** a rejected request depends on an account-scoped uploaded file or an owner-bound continuation without a verified self-contained fresh replay body
- **THEN** the proxy does not move the request to another account
- **AND** it preserves the original upstream rejection

### Requirement: Model-capacity messages are retryable transient failures

When upstream returns a temporary model-capacity failure whose message says that the selected model is at capacity, the proxy MUST treat the failure as retryable transient even if the upstream error code or HTTP status would otherwise look non-retryable.

#### Scenario: Selected model capacity with invalid request code is retryable

- **WHEN** upstream returns an error envelope with `error.message = "Selected model is at capacity. Please try a different model."`
- **AND** the normalized error code is `invalid_request_error`
- **AND** the HTTP status is `400`
- **THEN** `classify_upstream_failure` returns `failure_class = "retryable_transient"`
- **AND** pre-visible streaming/websocket paths are eligible to retry or fail over instead of surfacing a terminal client error.

#### Scenario: Serialized selected-model capacity event surfaces without replay

- **WHEN** a streaming Responses request receives a first upstream `response.failed` or `error` event whose message says the selected model is at capacity
- **AND** no downstream-visible output has been emitted
- **THEN** the proxy MUST surface that terminal event without transparently re-POSTing the request
- **AND** the absence of an upstream response id MUST NOT by itself prove the POST was safe to replay.

#### Scenario: Post-connect body-read disconnect is not replayed as capacity retry

- **WHEN** a streaming Responses request fails while reading the upstream stream body after the upstream request has been dispatched
- **AND** the failure is an `aiohttp` client error, timeout, EOF, or other transport/body-read close without typed pre-dispatch provenance
- **THEN** the proxy MUST surface the stream failure to the downstream client
- **AND** the proxy MUST NOT transparently re-POST the request as a model-capacity retry.

#### Scenario: Websocket connect failure retries before request dispatch

- **WHEN** an upstream websocket handshake raises a typed connector failure or connect timeout before the `response.create` frame is sent
- **THEN** the proxy MUST preserve typed pre-dispatch provenance and MAY retry or fail over before any downstream-visible output
- **AND** a websocket transport selection MUST NOT turn that failure into a terminal serialized SSE event.

#### Scenario: Direct HTTP TLS verification failure is not retried

- **WHEN** a direct HTTP stream raises a certificate or TLS connector failure before request dispatch
- **THEN** the proxy MUST surface the TLS failure without transparently retrying or failing over
- **AND** pre-dispatch provenance MUST NOT classify the non-transient TLS failure as retryable.

#### Scenario: Quota and rate-limit codes retain their stronger classification

- **WHEN** upstream returns a quota or rate-limit error code
- **THEN** the proxy MUST keep classifying it as quota or rate-limit before applying message-based model-capacity detection.

#### Scenario: Post-refresh transient exhaustion preserves every health signal

- **WHEN** one or more accounts each exhaust multiple same-account post-refresh transient retries before the request succeeds or terminates
- **THEN** the proxy MUST settle API-key usage before recording any deferred account-health failure
- **AND** each exhausted account MUST receive exactly one classified health failure plus one additional failure for every remaining exhausted retry
- **AND** selecting or exhausting a later account MUST NOT replace, lose, or duplicate an earlier account's deferred failures.

#### Scenario: Classified quota failures still use the model-capacity replay wait

- **WHEN** a replayable pre-created HTTP bridge request receives the selected-model capacity message with a quota or
  rate-limit error code
- **THEN** the proxy MUST preserve that quota or rate-limit classification for account health handling
- **AND** the proxy MUST still apply the model-capacity wait before replaying the request.

### Requirement: HTTP bridge model-capacity retry waits preserve stream contracts

The proxy MUST wait before replaying a pre-created HTTP bridge request with a selected-model capacity failure only
when the failure happened before any downstream-visible response event and the request is still replayable as a fresh
request.

#### Scenario: Public propagated-error streams do not receive pre-retry keepalives

- **WHEN** a `/v1/responses`-compatible HTTP bridge stream is configured to propagate startup HTTP errors
- **AND** upstream returns a selected-model capacity error before `response.created`
- **THEN** the proxy MUST NOT emit `codex.keepalive` or account-capacity wait events before the retry completes.

#### Scenario: Replay waits remain bounded by the original bridge deadline

- **WHEN** the selected-model capacity error arrives near or after the original bridge request deadline
- **THEN** the proxy MUST NOT start a fresh upstream replay after that deadline is exhausted.

#### Scenario: Only fresh replayable bridge requests wait

- **WHEN** the selected-model capacity error belongs to an anchored request that cannot be replayed without
  `previous_response_id`
- **THEN** the proxy MUST forward the terminal error promptly without sleeping for the model-capacity retry delay.

#### Scenario: Retry-safe injected anchors still wait

- **WHEN** the proxy injected `previous_response_id` and retained a fresh request body that is safe to replay without
  that anchor
- **AND** upstream returns a selected-model capacity error before visible output
- **THEN** the proxy MUST apply the model-capacity wait before stripping the injected anchor and replaying the fresh
  request.

#### Scenario: Remote-owner relay preserves the hidden startup wait

- **WHEN** an origin replica forwards a bridge request to its remote owner
- **THEN** the origin MUST keep its startup probe pending until the owner relay returns response headers or a terminal
  startup error
- **AND** a selected-model capacity wait on the owner MUST NOT cause the origin to commit HTTP 200 before that wait
  completes.

#### Scenario: Waiting keeps the retry tied to the pending request

- **WHEN** the proxy waits before replaying a selected-model capacity failure
- **THEN** the request MUST remain reserved in the bridge pending queue while it waits
- **AND** the proxy MUST retain the session response-create gate so a younger request cannot enter while the sole
  upstream reader is sleeping
- **AND** the proxy MUST release account-level and shared response-create capacity during the wait
- **AND** the proxy MUST reacquire both capacity leases before sending the replay
- **AND** the proxy MUST skip the replay if that queued request detaches before the wait completes.

### Requirement: WebSocket stale-anchor failures include diagnostic metadata
When a direct Responses WebSocket request fails closed because upstream rejects `previous_response_id` with `previous_response_not_found`, the service MUST emit stale-anchor diagnostic metadata in operator logs and request-log failure metadata. The metadata MUST distinguish `previous_response_source` (`client_supplied`, `proxy_injected`, or `unknown`), whether a fresh no-anchor replay body was available, owner lookup outcome/source, whether the matched previous response belongs to the same Codex session when known, and the previous-response age in seconds when known. The metadata MUST NOT expose raw `previous_response_id` values or request payload content.

#### Scenario: client-supplied stale anchor is classifiable
- **GIVEN** a direct WebSocket request arrives with a client-supplied `previous_response_id`
- **AND** upstream rejects that anchor with `previous_response_not_found`
- **THEN** the continuity failure log and request-log failure metadata identify `previous_response_source=client_supplied`
- **AND** they include owner lookup and replay-availability metadata without raw response ids

#### Scenario: proxy-injected stale anchor is classifiable
- **GIVEN** codex-lb injects a session-continuity `previous_response_id` into a direct WebSocket request
- **AND** upstream rejects that anchor with `previous_response_not_found`
- **THEN** the continuity failure log and request-log failure metadata identify `previous_response_source=proxy_injected`
- **AND** they state whether a retry-safe fresh no-anchor replay body was available
- **AND** owner lookup, age, and same-session fields remain explicit as `unknown` when unavailable rather than being omitted

#### Scenario: stale anchor owner hit records age and session relationship
- **GIVEN** owner lookup finds a previous response row for the rejected anchor
- **WHEN** the direct WebSocket request fails closed with `previous_response_not_found`
- **THEN** the stale-anchor diagnostics include the owner lookup source
- **AND** include previous-response age seconds and same-session status when those values can be derived

#### Scenario: account-only cache hits do not guess owner session metadata
- **GIVEN** owner resolution hits a request cache entry that retains the account id but not the matched request-log row
- **WHEN** the direct WebSocket request fails closed with `previous_response_not_found`
- **THEN** the stale-anchor diagnostics identify the owner lookup source as the request cache
- **AND** leave previous-response age and same-session status unknown rather than inferring them from the current request scope

### Requirement: Responses HTTP ingress uses the expanded bounded budget

HTTP requests to `/v1/responses` and `/backend-api/codex/responses`, including trailing-slash variants, MUST use the larger of `max_decompressed_body_bytes` and `max_decompressed_responses_body_bytes` as both the raw-body and decompressed-body ingress budget. The Responses-specific default MUST remain 128 MiB.

The trailing-slash variants MUST be hidden aliases of the canonical HTTP handlers rather than redirects, so streamed bodies receive the same admission, authorization, and route behavior.

If either representation exceeds that budget, the service MUST stop before route logic or upstream forwarding and return HTTP 413 with an OpenAI-compatible error envelope carrying `error.code = payload_too_large` and `error.type = invalid_request_error`.

This transport-ingress 413 applies before parsing and is distinct from the existing application-level oversized-`response.create` guard. A request that fits the 128 MiB transport budget but still exceeds the upstream websocket budget after historical slimming MUST retain the existing HTTP 400 `payload_too_large` behavior and `param = input`.

#### Scenario: Larger Responses request fits both ingress checks

- **WHEN** a Responses HTTP request is larger than the general budget but no larger than the Responses budget in either raw or decompressed form
- **THEN** the ingress guards allow the request to continue to Responses route handling

#### Scenario: Trailing-slash Responses request is admitted without redirect

- **WHEN** a client sends a chunked HTTP request to `/v1/responses/` or `/backend-api/codex/responses/`
- **THEN** the service applies the same ingress budget and handler as the corresponding canonical path
- **AND** it does not return a trailing-slash redirect before consuming the guarded body

#### Scenario: Responses raw body exceeds its budget

- **WHEN** a Responses HTTP request's raw body exceeds the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Responses expanded body exceeds its budget

- **WHEN** an encoded Responses HTTP request fits the raw budget but expands beyond the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Post-slimming application rejection remains 400

- **WHEN** a Responses HTTP request fits the raw and decompressed transport-ingress budget
- **AND** its serialized `response.create` still exceeds the upstream websocket budget after historical slimming
- **THEN** the existing application-level guard returns HTTP 400 with `error.code = payload_too_large`, `error.type = invalid_request_error`, and `error.param = input`

### Requirement: Thread-goal OpenAPI operations have unique stable identifiers
The generated OpenAPI document MUST assign a unique `operationId` to every documented HTTP operation. The GET and POST operations at `/backend-api/codex/thread/goal/get` MUST remain available through the same runtime behavior and MUST expose the deterministic identifiers `thread_goal_get_backend_api_codex_thread_goal_get_get` and `thread_goal_get_backend_api_codex_thread_goal_get_post`, respectively. Correcting this schema metadata MUST NOT change either method's authentication, dependency, request forwarding, upstream operation, response status, or response payload behavior.

#### Scenario: Full OpenAPI schema has unique operation identifiers
- **WHEN** an unauthenticated client requests `GET /openapi.json`
- **THEN** every documented HTTP operation has an `operationId`
- **AND** no two documented HTTP operations share an `operationId`

#### Scenario: Thread-goal methods publish deterministic identifiers
- **WHEN** an unauthenticated client inspects `/openapi.json`
- **THEN** `GET /backend-api/codex/thread/goal/get` has `operationId` `thread_goal_get_backend_api_codex_thread_goal_get_get`
- **AND** `POST /backend-api/codex/thread/goal/get` has `operationId` `thread_goal_get_backend_api_codex_thread_goal_get_post`

#### Scenario: Thread-goal runtime forwarding remains compatible
- **WHEN** a client invokes either GET or POST `/backend-api/codex/thread/goal/get` with valid existing dependencies
- **THEN** the request is forwarded through the existing thread-goal handler using the original request method
- **AND** the upstream operation, response status, and response payload remain unchanged

### Requirement: Public synthetic Responses failures carry numeric sequences

Public streaming `POST /v1/responses` MUST emit every terminal
`response.failed` with a finite integer `sequence_number` so
strict OpenAI SDK Responses parsers recognize the terminal failure. If the
upstream or proxy-generated event omits a finite integer sequence, the public
normalizer MUST assign the next sequence after all finite integer sequences it
has observed in the same downstream stream. If it also synthesizes a leading
`response.created` from that failure, the created event MUST consume the next
sequence and the failure MUST use the following sequence so both events have
distinct values. Otherwise, if no finite integer sequence has been observed,
failure numbering MUST begin at zero.

The public normalizer MUST preserve an existing finite integer
`sequence_number` and advance its next-sequence watermark accordingly. This
repair MUST NOT change Codex-private backend stream shapes.

#### Scenario: Bridge failure after reasoning remains parseable

- **GIVEN** public `/v1/responses` has emitted sequenced reasoning events
- **WHEN** the upstream bridge closes before a terminal response
- **THEN** the downstream terminal `response.failed` carries the next numeric
  `sequence_number`
- **AND** a strict OpenAI SDK parser recognizes it as a terminal failure

#### Scenario: Leading failure follows synthesized created sequence

- **GIVEN** public `/v1/responses` has not emitted a finite integer sequence
- **WHEN** an unsequenced leading `response.failed` requires a synthesized
  `response.created`
- **THEN** the created event carries `sequence_number = 0`
- **AND** the terminal failure carries `sequence_number = 1`

#### Scenario: Failure after an unsequenced created event starts at zero

- **GIVEN** public `/v1/responses` has emitted an unsequenced
  `response.created` and no finite integer sequence
- **WHEN** the proxy synthesizes a terminal `response.failed`
- **THEN** the terminal event carries `sequence_number = 0`

#### Scenario: Valid upstream failure sequence remains unchanged

- **GIVEN** an upstream terminal `response.failed` carries a finite integer
  `sequence_number`
- **WHEN** the public normalizer forwards the event
- **THEN** it preserves that sequence number unchanged
- **AND** if it must synthesize a leading `response.created`, that event uses
  the immediately preceding integer sequence

#### Scenario: Backend Codex stream shape remains unchanged

- **GIVEN** a Codex-private backend Responses stream carries an unsequenced
  terminal failure
- **WHEN** the stream is served without the public OpenAI SDK contract
- **THEN** the proxy does not add a public compatibility sequence

### Requirement: Direct WebSocket capability intent is trusted and private

A direct Responses WebSocket MUST recognize the exact internal marker
`X-Codex-LB-Required-Capability: trusted_cyber` only after successful existing
proxy API-key authentication. It MUST accept one marker from either the
handshake headers or the current `response.create.client_metadata`. Duplicate,
conflicting, non-string, unknown, malformed, or unauthenticated signals MUST
fail before account selection. Raw duplicate JSON keys or duplicate
`client_metadata` containers MUST NOT collapse into an ordinary request. The
marker MUST be rejected on every downstream frame type other than
`response.create`.

The proxy MUST remove the capability header and the exact consumed metadata
key before upstream dispatch, request archival, diagnostics, and logging.
Unrelated client metadata MUST remain unchanged.

#### Scenario: Per-frame intent routes before upstream open
- **WHEN** an authenticated frame carries the exact metadata marker on a
  downstream socket opened without the header
- **THEN** the proxy establishes REQUIRED before opening or reusing an upstream
  socket

#### Scenario: Ambiguous or untrusted signal fails closed
- **WHEN** a signal is duplicated, malformed, unknown, or lacks an authenticated
  proxy API-key principal
- **THEN** the proxy returns a typed error before account or model-source
  dispatch

#### Scenario: Duplicate JSON cannot erase intent
- **WHEN** raw JSON repeats the capability key or repeats `client_metadata`
  around a capability marker
- **THEN** the proxy returns the typed unsupported-capability error before
  selection

#### Scenario: Capability metadata on another frame is rejected
- **WHEN** a downstream frame other than `response.create` contains the
  capability metadata key
- **THEN** the proxy returns a typed error without forwarding or archiving that
  frame upstream
- **AND** malformed JSON text is rejected rather than passed through an already
  open upstream socket
- **AND** binary downstream frames are rejected before parsing, archiving, or
  upstream forwarding

#### Scenario: Internal metadata is not forwarded or archived
- **WHEN** a valid capability-bearing frame is dispatched and archived
- **THEN** neither capability carrier appears in upstream headers, upstream
  payload, archive payload, diagnostics, or logs

### Requirement: A late capability cannot reuse an ordinary upstream socket

A later REQUIRED frame MUST NOT reuse an upstream socket selected for an
ordinary request on the same downstream WebSocket. An idle ordinary socket
MUST be retired before capable
reselection. If another frame is still pending, the proxy MUST fail closed
rather than change the account requirement beneath in-flight work. The socket's
selection contract, not whether its account happened to have the capability
grant, MUST determine whether it was selected as ordinary. Before reusing a
REQUIRED-selected socket, the proxy MUST revalidate the pinned account and its
current capability grant through the canonical selector.

#### Scenario: Idle ordinary socket is replaced
- **WHEN** an idle downstream session previously selected an ordinary account
  and a later frame establishes REQUIRED
- **THEN** the ordinary upstream is retired before the frame is sent
- **AND** the replacement selection requires a security-work-authorized account

#### Scenario: Pending ordinary work blocks a requirement change
- **WHEN** ordinary work is still pending and a later frame establishes REQUIRED
- **THEN** the later frame fails before upstream send
- **AND** the pending frame's account and request state are not rewritten

#### Scenario: Revoked capability grant prevents socket reuse
- **WHEN** a socket was selected for REQUIRED but its pinned account's grant is
  no longer valid at canonical revalidation
- **THEN** the stale socket does not receive the next REQUIRED frame
- **AND** an idle socket is retired before constrained reselection

#### Scenario: Revalidation uncertainty fails closed
- **WHEN** canonical account revalidation cannot complete for a REQUIRED socket
- **THEN** the frame receives a typed capability-routing-unavailable error
- **AND** its reservation is settled without forwarding the frame upstream

### Requirement: Proof-gated recovery attempts are durably fenced

When an HTTP bridge request has a verified, account-neutral, unanchored full
resend body, the proxy MUST record that request fingerprint in the durable
recovery journal before dispatching it upstream. The record MUST be owned by
the current durable session owner epoch and MUST start in `unknown` state.
Requests without that replay-safety proof MUST NOT create a recovery-journal
record.

#### Scenario: Safe resend is journaled before dispatch

- **GIVEN** a request has a verified full-resend body that is safe to replay
  without `previous_response_id`
- **WHEN** the proxy admits the request for upstream dispatch
- **THEN** the durable journal contains one `unknown` record for its session
  and request fingerprint before `response.create` is sent

#### Scenario: Suppressed request is not journaled

- **GIVEN** a hard session retry circuit is cooling down
- **WHEN** the request is rejected before upstream dispatch
- **THEN** no recovery-journal record is created or refreshed

### Requirement: Durable replay is limited to ambiguous transport outcomes

The proxy MUST consume an `unknown` recovery-journal record for a fresh
account-neutral replay only after an ambiguous transport outcome, represented
by `stream_incomplete`, `stream_idle_timeout`, or
`upstream_request_timeout`, and only before any response event or downstream
output. Explicit deterministic `response.failed` errors MUST settle normally
and MUST NOT trigger a cross-account replay or consume the recovery fence.

#### Scenario: Transport ambiguity permits one replay

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** the upstream closes or times out before any response event
- **WHEN** the bridge handles the ambiguous transport failure
- **THEN** the record is atomically claimed and the request is replayed once
  on a fresh account-neutral upstream session

#### Scenario: Deterministic failure is not replayed

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** upstream emits an explicit pre-output `response.failed` such as an
  invalid request or quota rejection
- **WHEN** the bridge handles that terminal event
- **THEN** it forwards the terminal failure
- **AND** it leaves the journal available for settlement without replaying on
  another account

### Requirement: Recovery journal settlement is owner-fenced and idempotent

After a replayed request reaches `response.completed`, the proxy MUST mark its
journal record `replayed` only through the current durable owner epoch and
MUST retain the downstream response id when available. Repeated settlement,
stale owners, and concurrent claim attempts MUST NOT produce a second replay.
The migration MUST be on the current Alembic head and startup schema checks
MUST require the journal table.

#### Scenario: Completed replay settles once

- **GIVEN** a replayed request completes successfully
- **WHEN** the completion event is processed
- **THEN** the matching journal record becomes `replayed`
- **AND** a later retry cannot claim it again

#### Scenario: Stale owner cannot settle or replay

- **GIVEN** a journal record belongs to a newer durable owner epoch
- **WHEN** an old replica attempts settlement or replay
- **THEN** the operation is rejected without changing the record state

### Requirement: Claimed HTTP bridge completed queues remain deliverable

When HTTP bridge processing of `response.completed` removes a request from
pending ownership, it MUST retain the request's downstream event queue for the
remainder of that completed operation. Later asynchronous bookkeeping or
request detachment MUST NOT revoke that claimed queue before the completed
operation's selected terminal event and end-of-stream marker are enqueued. If
fail-closed bookkeeping replaces the upstream completion with a terminal
failure, that selected failure event is the terminal event governed by this
requirement.

While the claimed completed-delivery operation remains active, ordinary stream
idle accounting MUST NOT replace the upstream completion with a synthetic idle
failure, and the stream MUST continue emitting its existing liveness frames.
The completed-queue claim and the terminal idle-timeout decision MUST be
serialized under the bridge pending lock. If completed processing wins that
serialization and claims a live queue, the timeout MUST be suppressed. If the
terminal event and end-of-stream marker are already queued when a concurrent
timeout finishes awaited recovery work, the completed claim MUST remain
authoritative until the stream consumes that queued delivery. If the
terminal idle timeout wins while no completed delivery is active, it MUST
revoke the request's mutable event queue before releasing the pending lock so a
later completed event cannot claim an orphaned queue.

The first idle-timeout suppression for one completed-delivery operation MUST
emit one bounded diagnostic containing the request ID, downstream response ID,
and elapsed seconds. Further liveness intervals for that same operation MUST
NOT repeat the diagnostic.

When that operation returns, raises, or is cancelled before delivery, idle
timeout behavior MUST resume.

If detachment removes the request from pending ownership first, existing
client-disconnect and drain behavior MUST remain unchanged.

#### Scenario: Completed processing claims the request before detachment

- **GIVEN** an HTTP bridge stream is waiting on its request event queue
- **AND** an upstream `response.completed` event removes that request from pending ownership
- **WHEN** request detachment overlaps later completed-event bookkeeping
- **THEN** the stream receives the terminal event selected for downstream delivery exactly once
- **AND** the stream receives its end-of-stream marker

#### Scenario: Completed bookkeeping exceeds the idle window

- **GIVEN** completed-event processing has claimed a live request queue
- **WHEN** later completed bookkeeping exceeds the configured stream idle window
- **THEN** the stream continues emitting liveness frames
- **AND** it does not emit a synthetic idle failure while that operation remains active
- **AND** it logs the suppression once with request, response, and elapsed-time context

#### Scenario: Terminal idle timeout wins before completed processing

- **GIVEN** an HTTP bridge stream has exhausted its configured idle window
- **AND** no completed-delivery operation has claimed its queue
- **WHEN** the stream acquires the bridge pending lock before a concurrent completed event
- **THEN** it revokes the mutable event queue while still holding that lock
- **AND** it emits the existing synthetic idle failure
- **AND** later completed processing does not deliver to the revoked queue

#### Scenario: Completed delivery finishes during timeout recovery

- **GIVEN** an HTTP bridge timeout path is awaiting pre-response recovery work
- **AND** completed processing claims the live queue and enqueues its terminal event and end-of-stream marker
- **WHEN** completed processing returns before the timeout path rechecks ownership
- **THEN** the completed claim remains authoritative
- **AND** the stream consumes the queued completion without emitting a synthetic idle failure

#### Scenario: Completed bookkeeping aborts

- **GIVEN** completed-event processing has claimed a live request queue
- **WHEN** that completed-delivery operation exits without enqueueing its terminal event
- **THEN** idle timeout suppression ends
- **AND** the existing idle-timeout failure behavior resumes

#### Scenario: Detachment claims the request first

- **GIVEN** an HTTP bridge request is still pending
- **WHEN** detachment removes downstream queue ownership before completed-event matching
- **THEN** existing client-disconnect and upstream-drain behavior is preserved
- **AND** no completed event is delivered to another request

### Requirement: Replayed tool-call namespace metadata is local-only on upstream input

For standard and compact Responses requests, the proxy MUST omit `namespace` from every replayed `input` item whose `type` is `function_call`, `custom_tool_call`, or `apply_patch_call` before forwarding the request upstream. The proxy MUST preserve all other fields on that item, MUST retain the original namespace metadata for local call-identity and replay-deduplication processing, and MUST NOT alter client-provided top-level tool entries as part of this normalization.

#### Scenario: Standard Responses replay omits tool-call namespaces upstream

- **WHEN** a standard Responses request replays `function_call` and `custom_tool_call` input items with `namespace`
- **THEN** the upstream payload omits only those items' `namespace`
- **AND** preserves their remaining call fields
- **AND** the local request input retains the namespace metadata

#### Scenario: Compact Responses replay omits tool-call namespace upstream

- **WHEN** `/v1/responses/compact` replays a recognized tool-call input item with a namespace
- **THEN** its upstream payload omits the input item's `namespace`
- **AND** preserves the remaining tool-call fields

#### Scenario: WebSocket response.create omits tool-call namespaces upstream

- **WHEN** a Responses WebSocket request replays namespaced `function_call` and `custom_tool_call` input items
- **THEN** the upstream `response.create` frame omits only those items' `namespace`
- **AND** preserves their remaining call fields

#### Scenario: Configured Responses model source omits tool-call namespaces upstream

- **WHEN** `/v1/responses` routes a replayed namespaced tool call to a configured OpenAI-compatible Responses model source
- **THEN** the source payload omits only the call item's `namespace`
- **AND** preserves source-compatible request fields that the Codex upstream path does not support

#### Scenario: Account-neutral replay classification retains namespace identity

- **WHEN** an HTTP bridge evaluates a namespaced tool-call history for cross-account replay safety
- **THEN** the classifier input retains the namespace metadata
- **AND** the request fails closed rather than becoming account-neutral because of wire normalization

#### Scenario: Malformed replay item type does not fail serialization

- **WHEN** a permissively parsed input item has a non-string `type` and a `namespace`
- **THEN** outbound serialization does not raise an internal type error
- **AND** does not treat the item as a recognized replayed tool call

#### Scenario: Top-level namespace tool remains byte-preserved

- **WHEN** the client includes a top-level tool entry whose `type` is `namespace`
- **THEN** standard Responses serialization forwards that tool entry byte-identically

### Requirement: Responses-Lite replay proof tolerates only verified developer interleaving

When a fresh durable HTTP bridge classifies a client-unanchored Responses-Lite
full resend whose `additional_tools` bundle preserves developer messages inline,
the replay proof MUST tolerate a developer message only in the historical and
fresh positions defined below. Every other developer position or shape MUST
remain fail-closed.

A tolerated fresh developer message MUST have `type` omitted or equal to `message`,
MUST have role `developer`, MUST have no non-empty response-owned ID or phase,
MUST have no status or a `completed` status, MUST contain exact account-neutral
metadata with one nonblank `turn_id`, MUST contain exactly one self-contained
`input_text` content part, and MUST contain no unknown or account-scoped fields.
Explicit null or malformed item types MUST fail closed.

Classification MUST retain response-owned developer-message ID evidence until
these checks have completed, even when other response-owned IDs are projected
out. It MUST retain developer-role items before applying projection rules that
normally omit their declared item type, so a malformed developer item cannot
disappear before validation. A canonical Lite-prefix developer instruction MAY
appear immediately after the `additional_tools` bundle when it passes the same
account-neutral item checks as historical interleaving and has no response-owned
ID. A developer message in the stored prefix outside that canonical position or
the verified pending-call/matching-output interleave MUST fail closed. Non-Lite
`input` or `messages` forms whose instruction-role messages are normalized into
top-level `instructions` remain outside this requirement.

#### Scenario: Canonical Responses-Lite prefix remains transparent

- **GIVEN** a fingerprint-verified stored prefix begins with an `additional_tools` bundle
- **AND** a valid account-neutral developer instruction appears immediately after that bundle
- **WHEN** exact manifest or retained-output replay proof validates the stored prefix
- **THEN** the canonical developer instruction is transparent
- **AND** the original full input remains eligible for account-neutral replay

#### Scenario: Verified historical Responses-Lite developer message is transparent

- **GIVEN** a Responses-Lite input contains an `additional_tools` bundle
- **AND** its fingerprint-verified stored prefix contains a supported direct call
- **AND** a valid developer message appears before that call's matching output
- **AND** the fresh suffix exactly settles the durable pending-tool manifest
- **WHEN** the HTTP bridge opens a replacement session on the durable owner
- **THEN** it sends the original full input without injecting `previous_response_id`
- **AND** it sends the request once

#### Scenario: Other historical messages remain fail-closed

- **GIVEN** a supported direct call is pending in the verified stored prefix
- **WHEN** a user, assistant, system, malformed developer, or response-owned message appears before its output
- **THEN** exact manifest proof fails

#### Scenario: Other stored developer positions remain fail-closed

- **GIVEN** a fingerprint-verified stored prefix has no pending direct call
- **WHEN** a developer message appears outside the canonical adjacent Lite-prefix position
- **OR** the adjacent message has a response-owned ID
- **THEN** exact manifest and retained-output proofs fail

#### Scenario: Projection-omitted developer type remains visible to validation

- **GIVEN** a developer-role item declares a type normally omitted by replay projection
- **WHEN** account-neutral replay classification projects the full resend
- **THEN** the malformed developer item remains visible to replay proof
- **AND** replay classification fails closed

#### Scenario: Historical output remains mandatory

- **GIVEN** a valid developer message follows a supported historical call
- **WHEN** the matching output is missing or has another call ID or type
- **THEN** exact manifest proof fails

#### Scenario: Historical developer interleaving is bounded to one call and one message

- **GIVEN** a fingerprint-verified stored prefix opens a pending direct-call window
- **WHEN** that window holds more than one outstanding call at any point before the developer message
- **OR** a further call opens in that window after it has consumed a developer message
- **OR** a second developer message appears while the same window is still open
- **THEN** exact manifest proof fails
- **AND** a later window that holds exactly one outstanding call may still interleave one developer message

#### Scenario: Fresh developer suffix bounds are measured on the projected input

- **GIVEN** account-neutral replay classification projects the full resend
- **WHEN** the projection omits reasoning or completed bookkeeping items from the fresh suffix
- **THEN** the fresh developer suffix and terminality bounds are evaluated on the projected positions
- **AND** the accepted width is limited to shapes whose projected suffix satisfies those bounds

#### Scenario: Bounded fresh custom-tool developer interleave is transparent

- **GIVEN** the fingerprint-verified stored prefix is followed by a fresh suffix
- **AND** the durable pending-tool manifest contains exactly one `custom_tool_call`
- **WHEN** the entire suffix is exactly that custom call, one valid developer message, and its matching custom-tool output
- **THEN** exact manifest proof passes
- **AND** the original full input is sent once without injecting `previous_response_id`

#### Scenario: Other fresh tool-loop developer positions remain fail-closed

- **GIVEN** a durable pending-tool manifest
- **WHEN** a fresh developer message is used with a function or apply-patch call, appears in a parallel batch, is duplicated, lacks exact metadata, contains malformed or account-scoped content, or has leading or trailing suffix items
- **THEN** exact manifest proof fails

#### Scenario: Bounded retained-output developer follow-up is transparent

- **GIVEN** the fingerprint-verified stored prefix is followed by a completed assistant `final_answer`
- **AND** exactly one explicit user message follows that retained output
- **WHEN** one valid developer message is the terminal suffix item
- **THEN** retained-output proof passes
- **AND** the original full input is sent once without injecting `previous_response_id`

#### Scenario: Unproven retained-output developer follow-up remains fail-closed

- **GIVEN** a retained-output full resend
- **WHEN** the latest assistant output is not `final_answer`, the developer message is not terminal, the fresh input is raw or contains multiple user items, the developer metadata or content is not account-neutral, or the stored prefix contains historical developer interleaving
- **THEN** retained-output proof fails

### Requirement: Aborted terminal bookkeeping settles claimed reservations exactly once

The HTTP bridge MUST settle a request's API-key reservation exactly once even
when terminal-event bookkeeping aborts after removing the request from pending
ownership; that bookkeeping continuation exclusively owns the settlement. If
the continuation raises or is cancelled before finalization transfers that
settlement, the abort path MUST settle every request it still owns: the
reservation heartbeat MUST be cancelled, the
reservation MUST be released, and the downstream waiter SHOULD be unblocked
with an end-of-stream marker instead of waiting for its idle timeout. The
abort settlement MUST run to completion under cancellation (shielded), MUST
apply to the grouped previous-response error path's not-yet-finalized
remainder, and MUST NOT settle requests that a retry branch restored to
pending ownership. Settlement MUST remain idempotent so an abort overlapping
an already-transferred finalization cannot double-account usage.

If the abort settlement itself fails, the claim MUST be marked abandoned and
request detachment MUST be allowed to reclaim that settlement even though the
request is no longer in pending ownership. Detachment MUST NOT settle a live
claim whose bookkeeping continuation is still running.

#### Scenario: Completed bookkeeping raises after the pending pop

- **GIVEN** an upstream `response.completed` event has removed a request with an API-key reservation from pending ownership
- **WHEN** later completed bookkeeping raises before finalization
- **THEN** the reservation heartbeat task finishes
- **AND** the API-key reservation is released exactly once
- **AND** no reservation heartbeat touch runs afterward

#### Scenario: Completed bookkeeping is cancelled after the pending pop

- **GIVEN** an upstream `response.completed` event has removed a request with an API-key reservation from pending ownership
- **WHEN** the bookkeeping continuation is cancelled before finalization
- **THEN** the shielded abort settlement still cancels the heartbeat and releases the reservation
- **AND** the cancellation is re-raised after settlement

#### Scenario: Grouped previous-response finalization aborts mid-loop

- **GIVEN** a grouped previous-response error has removed multiple requests from pending ownership
- **WHEN** finalization aborts after settling only a prefix of those requests
- **THEN** every not-yet-finalized request in the group has its heartbeat cancelled and its reservation released

#### Scenario: Detachment reclaims an abandoned claim

- **GIVEN** terminal bookkeeping claimed a request out of pending ownership, aborted, and its abort settlement failed
- **WHEN** the downstream stream detaches that request
- **THEN** detachment cancels the heartbeat and releases the reservation even though the request is not in pending ownership

#### Scenario: Detachment leaves a live claim to its owner

- **GIVEN** terminal bookkeeping has claimed a request out of pending ownership and is still running
- **WHEN** the downstream stream detaches that request
- **THEN** detachment does not release the reservation out from under the in-flight finalization

### Requirement: Pool usage exhaustion is reported as a usage-limit error

The proxy MUST report pool-wide Responses usage exhaustion as a usage-limit
error. When every account eligible for a Responses request is exhausted by known
usage windows, the proxy MUST reject the request with HTTP `429` and an
OpenAI-style error envelope whose `error.code` and `error.type` are both
`usage_limit_reached`. If account selection has an authoritative upstream reset
timestamp for the exhausted pool, the response envelope MUST include that
timestamp as `error.resets_at`; the proxy MUST NOT expose the capped
human-facing retry hint or a synthesized fallback as `error.resets_at`. The
proxy MUST NOT collapse this condition into generic `no_accounts`,
`server_error`, or HTTP `503` semantics. Exhaustion classification MUST be
based on structured account state after the same eligibility filtering as
ordinary selection, and MUST NOT reclassify local capacity or overload codes
(account caps, admission gates, fair-share throttles) as usage exhaustion.

#### Scenario: Public Responses request exhausts the eligible usage pool

- **WHEN** account selection for a public `/v1/responses` or
  `/backend-api/codex/responses` request finds only usage-exhausted eligible
  accounts
- **THEN** the response status is HTTP `429`
- **AND** the response body has `error.code = "usage_limit_reached"`
- **AND** the response body has `error.type = "usage_limit_reached"`
- **AND** any selected pool reset timestamp is surfaced as `error.resets_at`

#### Scenario: Streaming selection failure preserves usage-limit semantics

- **WHEN** a streaming Responses request cannot select an account because every
  eligible account is usage-exhausted before downstream-visible output
- **THEN** the terminal error event uses `usage_limit_reached`
- **AND** clients do not receive a generic no-account/server-unavailable error

#### Scenario: Usage-limit selection failures are terminal, not waitable

- **WHEN** account selection fails with `usage_limit_reached` on a streaming,
  HTTP-bridge, or WebSocket Responses path
- **THEN** the proxy reports the structured usage-limit failure immediately
- **AND** it does not enter an account-capacity recovery wait for the
  remaining request budget before reporting it

#### Scenario: Local capacity codes keep their rate-limit contract

- **WHEN** account selection fails with a local capacity or overload code such
  as `account_stream_cap` or `account_response_create_cap`
- **THEN** the response keeps HTTP `429` with `error.type = "rate_limit_error"`
  and the stable local error code
- **AND** the response is not reported as `usage_limit_reached`

#### Scenario: Unusable non-exhausted pools keep existing semantics

- **WHEN** every account is paused, deactivated, or requires re-authentication
  and no eligible account is exhausted by a known usage window
- **THEN** the pre-existing `no_accounts` failure semantics are preserved

#### Scenario: Owner-scoped exhaustion preserves continuity semantics

- **WHEN** a request is pinned to a previous-response or file owner account and
  only that owner is usage-exhausted while the wider eligible pool is usable
- **THEN** the proxy keeps the existing continuity-owner failure semantics
- **AND** it does not report pool-wide `usage_limit_reached`

### Requirement: Silent HTTP bridge sessions are quarantined from re-attach and reuse

When an HTTP bridge session proves silent/wedged, the proxy MUST quarantine its session key for a bounded window so later requests stop attaching to it. A session proves silent/wedged when either (a) a pending request being failed or retired carried a proxy-injected `previous_response_id`, had sent `response.create`, observed upstream response events, and never had `response.created` assigned, or (b) the session key hits two consecutive eventless `missing_response_created_timeout` retires. This holds for every path that fails or retires the request — partial stale-holder cleanup, the reader-failure funnel, and direct all-stale session retirement alike. The quarantine MUST be evaluated only when a request is already being failed or its session retired — never against a live owned turn — so a stream whose `response.created` was observed (including deferred-reasoning streams with long event gaps) MUST NOT be quarantined, and mere event silence during an owned live turn MUST NOT trigger quarantine by itself.

While a session key is quarantined: an existing session under that key MUST NOT be selected for reuse (a new request detaches it and proceeds on a fresh session), and for durable-anchor selection a quarantined session that is still open MUST count as absent, exactly as if it were already gone. The quarantine registry verdict is authoritative for the key: any session under the key while the quarantine window is active — including a freshly created replacement whose own completion has not yet cleared the quarantine — is equally excluded from reuse and equally absent for anchor selection. A fresh reattach whose incoming payload already looks like a full conversation resend MUST NOT receive a proxy-injected durable anchor through any injection point — the fresh-reattach injection, session-state hydration of the durable anchor, or the session-level injection — so the dispatch goes upstream genuinely unanchored with the client's own untrimmed payload. A payload that does not look like a full resend (a genuine delta-only continuation) MUST still receive the durable anchor, because it has no other way to convey prior conversation state.

Quarantine state MUST be bounded and self-recovering: it is in-memory and session-scoped, expires by TTL (a live session that outlives its quarantine window MUST become reusable again), is cleared when a response completes on the same session key, and MUST NOT write account health or alter account selection.

#### Scenario: Reattach streams events but response.created is never assigned (#1534)

- **GIVEN** a durable HTTP bridge session with a stored anchor whose fresh reattach injected a proxy-owned `previous_response_id`
- **AND** the reattached upstream stream delivers response events but `response.created` is never assigned
- **WHEN** the stream fails or the session is retired with that request still pending
- **THEN** the request fails terminally as before
- **AND** the session key is quarantined with reason `reattach_missing_response_created`

#### Scenario: All-stale direct retirement still quarantines the key

- **GIVEN** a wedged reattach (proxy-injected `previous_response_id`, `response.create` sent, response events observed, `response.created` never assigned) that is the ONLY stale pending request on its session
- **WHEN** the stuck-gate watchdog retires the session directly instead of failing the stale holder individually
- **THEN** the session key is quarantined with reason `reattach_missing_response_created`
- **AND** the next request takes the fresh no-anchor path instead of rebuilding the identical anchored reattach

#### Scenario: Next request after the wedge completes on the fresh path

- **GIVEN** a session key quarantined after a reattach that streamed events without `response.created`
- **WHEN** a later request arrives for the same key with a full-conversation-resend payload and no client `previous_response_id`
- **THEN** the proxy does not inject the durable anchor for that request
- **AND** the request is sent upstream unanchored with the client's own full payload
- **AND** the request can complete normally instead of rebuilding the identical wedged reattach

#### Scenario: Suppressed anchor does not come back through session state

- **GIVEN** a quarantined session key and a full-conversation-resend payload whose stored durable prefix is trimmable but whose fresh suffix does not retain the prior output
- **WHEN** the fresh-reattach durable-anchor injection is skipped because of the quarantine
- **THEN** the durable anchor is not rehydrated into the fresh session's completed-response state
- **AND** the session-level injection does not re-add the same anchor or trim the stored prefix
- **AND** the dispatch goes upstream genuinely unanchored with the client's untrimmed payload
- **AND** the suppression applies even when the fresh-reattach injection was already ineligible for other reasons (for example a conversation-scoped payload, a live alias session, or an active-owner forward that falls back to a local rebind)

#### Scenario: Quarantined session is excluded from reuse selection

- **GIVEN** a session marked quarantined that is still live or retained for admission handoff
- **WHEN** a new request looks up that session key
- **THEN** the session is not considered reusable
- **AND** the request proceeds on a fresh session instead
- **AND** a replacement session created under the same still-quarantined key is likewise not reusable until a completion or the TTL clears the quarantine

#### Scenario: Repeated eventless timeouts quarantine the key

- **GIVEN** a session key whose pending request already retired once with the eventless `missing_response_created_timeout`
- **WHEN** a subsequent attach on the same key retires with the same eventless timeout before any response completes on the key
- **THEN** the session key is quarantined with reason `repeated_eventless_timeout`
- **AND** the first timeout alone does not quarantine the key

#### Scenario: Deferred-reasoning live turn is never quarantined

- **GIVEN** an owned live turn whose `response.created` was observed and whose events flow with long gaps (deferred reasoning)
- **WHEN** its stream later fails or its session is retired
- **THEN** the session key is not quarantined
- **AND** later requests keep the existing reuse and anchor-injection behavior

#### Scenario: Delta-only payloads keep their anchor while quarantined

- **GIVEN** a quarantined session key — including one whose quarantined session is still open with other active requests
- **WHEN** a later request arrives whose payload does not look like a full conversation resend
- **THEN** the still-open quarantined session counts as absent for durable-anchor selection
- **AND** the durable anchor is still injected for that request, preserving the client's only way to convey prior context

#### Scenario: Quarantine is bounded and self-clearing

- **GIVEN** a quarantined session key
- **WHEN** a response completes on that session key, or the quarantine TTL elapses
- **THEN** the quarantine (and its eventless strike counter) is cleared
- **AND** a session that survived the quarantine window is reusable again instead of staying rejected forever
- **AND** no durable row, janitor work, or account-health write was involved at any point

### Requirement: Scoped operation identity

The system MUST include the normalized API-key scope in every durable HTTP
bridge operation fingerprint and MUST apply that scope to fingerprint and
completed-operation lookups.

#### Scenario: Equal requests from different keys remain isolated

- **WHEN** two API keys submit the same logical request
- **THEN** each key receives an independent durable operation identity

### Requirement: Recoverable startup takeover

Startup cleanup MUST retain sessions that own submitted, acknowledged, or
unknown operations and MUST detach ownership before a replacement instance
takes over.

#### Scenario: Restart preserves an in-flight operation

- **WHEN** an instance restarts while an operation is nonterminal
- **THEN** cleanup detaches the old owner without deleting the operation spool

### Requirement: Fresh retry transcript

When an explicit failed operation is rebound, the system MUST atomically remove
the prior operation events and reset event-byte/spool state before accepting new
events.

#### Scenario: Failed retry cannot replay stale failure output

- **WHEN** a failed operation is retried and later completes
- **THEN** replay contains only the new attempt's events

### Requirement: Proof-gated sibling anchoring

The system MUST advance a continuation to a completed sibling response only
when the sibling has the same parent and logical request fingerprint in the
same API-key scope.

#### Scenario: Distinct sibling input keeps its requested parent

- **WHEN** a request reuses a parent with a different fingerprint
- **THEN** the service does not silently anchor it to another child response

### Requirement: Single migration head

The Alembic graph MUST converge the durable operation revisions with the current
release head and MUST expose one canonical head after upgrade.

#### Scenario: Upgrade resolves one head

- **WHEN** migrations are upgraded to the release tip
- **THEN** Alembic reports one canonical head

### Requirement: Conservative spool defaults

New operation rows MUST start with an incomplete event spool on SQLite and
PostgreSQL. A transcript MUST become replayable only after terminal event drain
and explicit finalization.

#### Scenario: Nonterminal spool is not replayable

- **WHEN** an operation has events but no finalized terminal event
- **THEN** recovery does not replay its transcript as complete

### Requirement: Retain completed recovery transcripts

Startup ownership cleanup MUST retain sessions with operation transcripts that
remain inside the configured operation retention window, including completed
operations, and MUST let normal spool retention remove the operation rows.

#### Scenario: Recent completed transcript survives takeover

- **WHEN** startup cleanup sees a recent completed transcript
- **THEN** it retains the session until normal retention expires it

### Requirement: Continuous transcript retention

Operation transcript cleanup MUST run periodically in a leader-gated scheduler
and MUST drain all eligible batches during each pass. Disabling the existing
sticky-session mapping cleanup switch MUST NOT disable operation transcript
retention; that switch MAY skip sticky mapping maintenance while durable
operation retention continues.

#### Scenario: Retention drains all eligible batches

- **WHEN** more rows are eligible than one deletion batch
- **THEN** one scheduler pass removes every eligible batch

#### Scenario: Sticky cleanup toggle does not disable transcript retention

- **WHEN** sticky-session cleanup is disabled and the durable bridge schema is
  available
- **THEN** the leader-gated scheduler still drains expired operation transcript
  rows while skipping sticky mapping cleanup

### Requirement: Fresh indefinite-recovery spool

Before dispatching a server-owned retry for a nonterminal operation, the system
MUST atomically clear any partial event spool under the durable owner fence.

#### Scenario: Retry starts with a clean transcript

- **WHEN** an anchored retry is dispatched after partial persistence
- **THEN** old events and byte counts are cleared before new output is accepted

### Requirement: Ordered deferred reasoning persistence

Deferred reasoning events released before a visible event MUST be persisted in
the same order in which they are delivered downstream, before the visible
event is persisted.

#### Scenario: Deferred events preserve downstream order

- **WHEN** buffered reasoning is released before visible output
- **THEN** the durable spool stores the reasoning blocks before that output

### Requirement: Per-operation disconnect classification

When a shared bridge websocket closes, each pending operation MUST be
classified from that operation's own observed response-event count. Activity
from a sibling request MUST NOT make an eventless operation safely retryable.

#### Scenario: Sibling output does not acknowledge an eventless request

- **WHEN** one pending request emitted output and another emitted none
- **THEN** the two operations receive different disconnect classifications

### Requirement: Abandoned operation retention

Operation retention MUST expire stale submitted and acknowledged rows in
addition to terminal and ambiguous rows, so a crashed or abandoned operation
cannot retain raw request data indefinitely.

#### Scenario: Stale abandoned request is purged

- **WHEN** a submitted operation exceeds retention age
- **THEN** its request data and event spool are removed

### Requirement: Acknowledged alias persistence failure

If upstream has acknowledged a response but local continuity-alias persistence
fails, the downstream error MUST NOT transition the durable operation to a
retryable failed state. The operation MUST remain acknowledged/ambiguous so an
identical retry cannot dispatch a duplicate upstream turn.

#### Scenario: Alias write failure remains fail-closed

- **WHEN** an acknowledged response cannot publish its continuity alias
- **THEN** the operation remains non-retryable and the client receives a terminal error

### Requirement: Cross-session nonterminal handoff

When a scoped operation fingerprint is found under a different durable
session, a nonterminal operation MUST be atomically rebound to the currently
owned session before its event spool is reset or a recovery attempt is sent.
Completed replayable operations MUST remain attached to their original session.
The handoff MUST be refused while the prior session has an unexpired owner
lease, preventing concurrent owners from dispatching the same turn.

#### Scenario: Active prior owner fences handoff

- **WHEN** a duplicate request finds a nonterminal operation under another session
- **AND** that session still has an unexpired owner lease
- **THEN** the operation remains with the prior session and no concurrent retry is dispatched

#### Scenario: Expired prior owner permits handoff

- **WHEN** the prior session lease is absent or expired
- **THEN** the operation can be atomically rebound before recovery

### Requirement: Fenced one-shot recovery dispatch

The durable recovery journal MUST persist a one-shot replay budget for every
recovery-safe request. The budget MUST be consumed atomically when a replay is
claimed for dispatch, and a caller that proves the replay never reached the
upstream send boundary MUST restore that claim under the same session owner
fence. A replacement session MUST retain or transfer a fenced origin owner
until the claim is rolled back or settled; selecting a replacement or failing
preflight MUST NOT permanently consume an unsent replay.

#### Scenario: Concurrent reconnects consume one replay

- **WHEN** concurrent reconnects observe the same ambiguous operation
- **THEN** exactly one owner atomically claims the persisted replay budget and
  other reconnects fail closed without dispatching a duplicate

#### Scenario: Pre-dispatch replacement failure restores the budget

- **WHEN** a replay claim is made but replacement admission or preflight fails
  before the exact upstream frame is sent
- **THEN** the claim returns to the available state and the fenced origin
  owner is released only after that rollback succeeds

#### Scenario: Successful replacement settles the origin journal

- **WHEN** a replacement session dispatches the claimed replay and receives a
  terminal response event
- **THEN** settlement uses the retained origin owner fence before releasing it
  and the replay budget cannot be claimed again

### Requirement: Lease-aware operation retention

Retention MUST NOT delete stale submitted or acknowledged operations while
their session is actively owned with an unexpired lease. The owner/lease
predicate MUST be rechecked in the deletion transaction.

#### Scenario: Active lease protects stale operation

- **WHEN** a stale operation belongs to a session with a live lease
- **THEN** retention leaves it intact

### Requirement: Anchored indefinite recovery gate

The server-indefinite recovery loop MUST be installed only for an eventless
anchored continuation with a durable parent operation. Fresh first-turn
requests and streams that already emitted downstream response events MUST
terminate normally rather than being resent indefinitely.

#### Scenario: Fresh request is not held indefinitely

- **WHEN** a first-turn request loses its upstream connection
- **THEN** the proxy returns its normal error path without an indefinite loop

### Requirement: Retry reservation terminalization

If reacquiring API-key usage limits for a recovery attempt fails, the proxy
MUST settle the prior reservation and emit a terminal `response.failed` SSE
event instead of aborting the already-started stream.

#### Scenario: Quota failure produces terminal SSE

- **WHEN** a recovery retry cannot reacquire its usage reservation
- **THEN** the client receives `response.failed` and the prior reservation is settled

#### Scenario: Unexpected admission failure produces terminal SSE

- **WHEN** recovery admission raises an unexpected infrastructure error before
  a replacement stream starts
- **THEN** the client receives `response.failed` and the prior reservation is
  settled instead of receiving a truncated stream

### Requirement: Failure spool/state ordering

For an explicit deterministic failure, the proxy MUST persist the terminal SSE
block before exposing the durable operation as failed. The event append and
failed-state transition MUST use the same owner fence and transaction when the
durable repository supports it.

#### Scenario: Concurrent retry cannot reset an unspooled failure

- **WHEN** a response failure is being settled while an identical reconnect is
  admitted
- **THEN** the reconnect observes the terminal operation fence and cannot reset
  or mix the previous failure into a new transcript

### Requirement: Partial disconnect acknowledgement

When a bridge disconnects after an operation has emitted any response event but
before a terminal event, the durable operation MUST remain acknowledged or
ambiguous. It MUST NOT be classified as retryable failed solely because the
disconnect was non-terminal.

#### Scenario: Partial output is never resent as a fresh turn

- **WHEN** the upstream closes after `response.created` but before completion
- **THEN** the operation remains non-retryable

### Requirement: Retry output stops indefinite recovery

An indefinite recovery attempt MUST stop retrying once that attempt emits any
downstream response event, even if the attempt later fails with a retryable
transport error.

#### Scenario: Retry output prevents a second attempt

- **WHEN** a retry emits a data event and then times out
- **THEN** the server stops the indefinite loop instead of appending another response

### Requirement: Preserve repeated event occurrences

The durable event spool MUST preserve repeated identical SSE blocks as distinct
ordered occurrences. Event identity MUST include its operation-local sequence
position rather than content alone.

#### Scenario: Identical deltas replay twice

- **WHEN** two consecutive SSE blocks have identical text
- **THEN** both occurrences are present in the replay transcript

### Requirement: Stop event persistence during shutdown

Proxy shutdown MUST close the HTTP bridge event batcher and cancel its
background flusher before the process exits.

#### Scenario: Shutdown cancels the flusher

- **WHEN** the proxy service begins shutdown after queueing an event
- **THEN** the batcher's background task is cancelled and awaited

### Requirement: Classify response.incomplete as terminal

An anchored `response.incomplete` event MUST transition the durable operation to
an explicit terminal state and finalize its transcript so it is not left in an
unknown in-flight state.

#### Scenario: Incomplete response is replayable as terminal

- **WHEN** upstream emits `response.incomplete`
- **THEN** the operation is terminalized and its drained transcript is eligible for replay

### Requirement: Settle reservations before timeout health

When an eventless timeout retires a keyed bridge, the proxy MUST settle all
pending request reservations before recording the account timeout health signal.
If settlement fails, the health signal MUST NOT claim that cleanup completed.

#### Scenario: Failed reservation release does not poison health state

- **WHEN** the timeout cleanup cannot release a pending reservation
- **THEN** the account timeout signal is not recorded before that failure is surfaced

### Requirement: Replay finalized incomplete operations

A finalized `incomplete` operation transcript MUST be replayed for an identical
request and MUST NOT be reset or treated as an unknown in-flight operation.

#### Scenario: Reconnect receives stored incomplete transcript

- **WHEN** an identical request finds a finalized incomplete operation
- **THEN** the stored terminal transcript is delivered without a new upstream dispatch

### Requirement: Validate final response.create size

After adding durable operation metadata, the proxy MUST revalidate the exact
serialized `response.create` frame against the upstream size limit before
sending it.

#### Scenario: Metadata cannot create an oversized frame

- **WHEN** operation metadata makes the final frame exceed the configured limit
- **THEN** the request is rejected or slimmed before any upstream send

### Requirement: Fence same-session active operations

Server-indefinite recovery MUST NOT reset or redispatch a nonterminal operation
when another pending request in the same durable session still references that
operation. Submitted and acknowledged operations MUST remain fail-closed;
only an inactive `unknown` operation may enter a fresh recovery attempt.

#### Scenario: Active same-session operation is not duplicated

- **WHEN** a duplicate request finds a submitted operation still referenced by another pending request
- **THEN** the proxy refuses a second dispatch and preserves the existing spool

### Requirement: Responses routes preserve the Ultrafast service tier

Responses-compatible routes MUST accept the canonical `ultrafast` service tier and MUST forward it unchanged. When upstream reports the actual response tier, request logging MUST preserve `ultrafast` using the existing requested, actual, and billable tier contract.

#### Scenario: Explicit Ultrafast request is forwarded

- **WHEN** a client sends a Responses request with `service_tier: "ultrafast"`
- **THEN** the forwarded upstream payload contains `service_tier: "ultrafast"`

#### Scenario: Upstream confirms Ultrafast processing

- **WHEN** upstream completes a request with `response.service_tier: "ultrafast"`
- **THEN** the actual and billable request-log tiers are `ultrafast`

### Requirement: Account-bound retries remain on their dispatch owner

The proxy MUST bind a Responses request body that is not a canonical
account-neutral fresh replay to the account that first receives that exact
body. Every later selection for that request MUST treat the dispatch owner as a
strict required account across HTTP streaming, HTTP bridge, and direct
WebSocket transports.

The proxy MUST NOT exclude the dispatch owner and send the retained body to a
different account during stale-anchor recovery, retryable account failure,
Trusted Access migration or degradation, bridge reconnect, or WebSocket account
switching. If the required owner is unavailable, the proxy MUST fail closed
without dispatching the retained body to another account.

The proxy MAY perform one forced authentication refresh and replay a retained
account-bound body on the same dispatch owner. It MUST NOT use that refresh to
exclude the owner or migrate the body to another account, and a permanent
authentication failure MUST remain terminal for the bound body.

The proxy MAY clear the dispatch-owner binding only after verified recovery
replaces the exact wire body and the replacement passes the canonical
account-neutral-fresh-replay predicate. Removing `previous_response_id` alone
MUST NOT make retained account-scoped input portable.

Proxy-owned operation metadata that will be added at the send boundary MUST
remain bound to the current account unless an explicit operation-rebind path
replaces that identity before account selection. Installing a verified fresh
body and clearing its dispatch-owner binding MUST occur as one state
transition.

#### Scenario: Encrypted reasoning remains on its first dispatch account

- **GIVEN** account A first receives a Responses request containing encrypted
  reasoning or another account-scoped retained item
- **WHEN** a pre-visible retry excludes account A or requests a differently
  authorized account
- **THEN** the proxy does not dispatch the retained body to account B
- **AND** the retry fails closed when account A is unavailable

#### Scenario: Verified account-neutral fresh replay may change accounts

- **GIVEN** verified recovery removes a stale continuation anchor
- **AND** the exact replacement body contains only canonical account-neutral
  fresh input
- **WHEN** normal retry selection chooses account B
- **THEN** the proxy may dispatch the replacement body to account B

#### Scenario: Confirmed pre-dispatch failure does not create an owner

- **GIVEN** account A is selected for a nonportable Responses body
- **WHEN** transport evidence confirms the request failed before any upstream
  bytes were dispatched
- **THEN** the proxy does not record account A as the dispatch owner
- **AND** normal retry selection may dispatch the body first on account B

#### Scenario: HTTP bridge preserves payload ownership

- **GIVEN** an HTTP bridge request has already dispatched a nonportable body to
  account A
- **WHEN** pre-created recovery or reconnect selection excludes account A
- **THEN** the bridge does not submit that body on account B

#### Scenario: Direct WebSocket preserves payload ownership

- **GIVEN** a direct WebSocket request has already dispatched a nonportable body
  to account A
- **WHEN** retry handling prepares an account switch
- **THEN** the proxy rejects the switch unless the exact replacement body is a
  canonical account-neutral fresh replay

#### Scenario: Bound authentication refresh stays on the owner

- **GIVEN** a nonportable body is bound to account A
- **WHEN** account A reports a refreshable authentication failure before
  visible output
- **THEN** the proxy may refresh and replay once on account A
- **AND** it does not dispatch the retained body to account B

#### Scenario: HTTP bridge operation identity remains on its owner

- **GIVEN** an HTTP bridge retry retains a proxy-owned operation identity
- **AND** no explicit operation rebind has replaced that identity
- **WHEN** retry selection evaluates another account
- **THEN** the bridge requires the current operation owner

#### Scenario: Existing settlement ordering is unchanged

- **GIVEN** an API-key reservation requires settlement during the failed retry
- **WHEN** account health is updated
- **THEN** required settlement still completes before deferred health writes

### Requirement: Compact terminal SSE errors preserve top-level error type

When the compact Responses upstream terminates with a top-level SSE `type=error` frame, the proxy MUST preserve a supplied non-blank `error_type` in the emitted OpenAI error envelope. If `error_type` is absent, non-string, or blank, the proxy MUST use `server_error`. The proxy MUST preserve existing status, code, message, and parameter mapping, and MUST NOT alter nested OpenAI-style error-envelope behavior.

#### Scenario: Top-level invalid request type is preserved

- **WHEN** compact upstream terminates with a top-level `type=error` frame whose `error_type` is `invalid_request_error`
- **THEN** the proxy returns HTTP 400 with `error.type=invalid_request_error`
- **AND** preserves the frame's code, message, and parameter

#### Scenario: Missing or blank top-level type uses compatibility fallback

- **WHEN** compact upstream terminates with a top-level `type=error` frame whose `error_type` is absent or blank
- **THEN** the emitted OpenAI error envelope uses `error.type=server_error`
- **AND** existing status, code, message, and parameter mapping remains unchanged

#### Scenario: Nested compact error envelope remains unchanged

- **WHEN** compact upstream terminates with a nested OpenAI-style error envelope
- **THEN** the proxy preserves the nested type and all other mapped fields using the existing parser
