# proxy-runtime-observability Specification

## Purpose

Define proxy observability contracts so runtime failures, routing decisions, and admission rejections remain diagnosable.
## Requirements
### Requirement: Proxy 4xx/5xx responses are logged with error detail
When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console. For local admission rejections, the log MUST also include the rejection stage or lane.

#### Scenario: Local admission rejection is logged
- **WHEN** the proxy rejects a request locally because a downstream or expensive-work admission lane is full
- **THEN** the console log includes the local response status, normalized error code and message
- **AND** it includes which admission lane or stage rejected the request

### Requirement: Continuity-sensitive responses flows emit explicit operator diagnostics
When the proxy resolves or fails closed a continuity-sensitive follow-up request, the system MUST emit structured diagnostics that let operators determine how continuity ownership was resolved or why the proxy returned a retryable masked error.

#### Scenario: owner resolution source is recorded for a previous-response follow-up
- **WHEN** a websocket, HTTP fallback, or HTTP bridge follow-up request includes `previous_response_id`
- **AND** the proxy resolves the required owner account from a continuity source such as a local bridge session, owner cache, or request-log lookup
- **THEN** the system emits a structured diagnostic describing the continuity surface, source, and outcome
- **AND** the diagnostic does not expose the raw `previous_response_id`

#### Scenario: fail-closed continuity masking is recorded
- **WHEN** the proxy rewrites or returns a retryable continuity error because owner metadata is unavailable, continuity state is lost, or the pinned owner account is unavailable
- **THEN** the system emits a structured diagnostic describing the continuity surface and fail-closed reason
- **AND** Prometheus counters record the low-cardinality source or reason labels for that decision

### Requirement: Full upstream conversation archive
The proxy MUST provide an opt-in durable archive of Codex-to-upstream conversation traffic. When enabled, the archive MUST write gzip-compressed newline-delimited JSON records for upstream request payloads, streamed Responses events, compact response payloads, and websocket text or binary frames without performing gzip file I/O in the request event loop during normal operation. The archive writer queue MUST be bounded and MUST apply synchronous write backpressure instead of growing without limit when the background writer is saturated. Archive records MUST include request id, timestamp, direction, traffic kind, transport, account id when known, upstream target metadata, redacted headers, and the full payload or frame body. Credential-bearing headers such as authorization, cookies, proxy authorization, token headers, and API key headers MUST be redacted before persistence. JSON records MUST preserve non-ASCII payload text as UTF-8 rather than Unicode escape sequences. When disabled, no archive file MUST be created by the archive writer. Request-log API rows MUST expose an `archiveRequestId` lookup key when the persisted log id can differ from the archive record request id.

#### Scenario: operator enables archive for audit
- **WHEN** `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED=true`
- **AND** a Codex Responses request is proxied upstream
- **THEN** the archive records both the outbound upstream payload and inbound upstream events or response body as gzip JSONL
- **AND** credential-bearing headers are stored as redacted values

#### Scenario: archive remains disabled by default
- **WHEN** the archive setting is not enabled
- **THEN** the archive writer does not create conversation archive files

#### Scenario: operator views archived traffic
- **GIVEN** conversation archive files exist as `.jsonl.gz` or legacy `.jsonl`
- **WHEN** an authenticated dashboard operator opens an existing request log detail
- **THEN** the dashboard can find matching archive records by request id across archive files and display payload plus metadata for that request

#### Scenario: response-id request logs keep archive lookup
- **WHEN** a successful proxied request stores a downstream response id in the request-log `requestId`
- **AND** the conversation archive stored records under the original request context id
- **THEN** the request-log API response includes `archiveRequestId` with the original archive lookup id
- **AND** the persisted `requestId` remains available for response-id continuity lookup

### Requirement: Optional upstream payload tracing
When request-shape tracing for proxy routing is enabled, the system MUST log affinity decision metadata without exposing full prompt text or full cache keys. The trace MUST include request id, request kind, sticky kind, sticky-key source, whether a session header was present, whether a prompt-cache key was set/injected, and a stable tools hash when tools are present.

#### Scenario: Affinity request-shape tracing is enabled
- **WHEN** the proxy resolves routing for a Responses or compact request while request-shape tracing is enabled
- **THEN** the console shows the chosen sticky kind, sticky-key source, prompt-cache-key presence/injection state, and tools hash
- **AND** the console does not log raw prompt text or the full prompt-cache key unless the explicit raw-key flag is enabled

### Requirement: Proxy exposes runtime observability for bridge routing decisions
The service MUST expose metrics and structured logs for HTTP bridge routing decisions so operators can distinguish hard owner handoff from soft locality misses.

#### Scenario: owner forward metrics are emitted
- **WHEN** a hard continuity bridge request is forwarded to the owner replica
- **THEN** the service emits owner-forward counters for success or failure
- **AND** it records bridge forward latency

#### Scenario: soft locality misses are observable
- **WHEN** a prompt-cache bridge request lands on a non-owner replica and rebinds locally
- **THEN** the service emits locality miss and local rebind observability
- **AND** it logs a structured bridge event indicating soft locality rebind

### Requirement: Responses concurrency pressure is observable

The service MUST expose low-cardinality logs and metrics for account-local in-flight create count, active stream count, leased token/cost pressure, cap rejections, lease stale reclaims, soft-affinity reroutes, and local-vs-upstream 429 classification. Observability MUST avoid raw prompt text, raw affinity keys, API keys, emails, request ids, session ids, and request payload content.

The service MUST expose a Prometheus gauge named `codex_lb_account_inflight_leases` labeled by `account_id` and `kind`, where `kind` is either `stream` or `response_create`. The gauge value MUST equal the current in-process account lease count for that account and kind. The gauge MUST update when a lease is acquired, explicitly released, or reclaimed as stale. Gauge labels MUST NOT include raw prompt text, raw affinity keys, API keys, emails, request ids, session ids, or request payload content.

#### Scenario: Local and upstream 429s are separated

- **WHEN** local admission rejects a request and upstream later returns a rate limit for another request
- **THEN** logs and metrics distinguish local overload reasons from normalized upstream `upstream_rate_limit`
- **AND** preserved upstream wire payloads may retain upstream codes such as `rate_limit_exceeded`, `usage_limit_reached`, or `insufficient_quota`

#### Scenario: Active account leases update gauge

- **WHEN** the proxy acquires a `stream` lease for account `acc_1`
- **THEN** `codex_lb_account_inflight_leases{account_id="acc_1",kind="stream"}` increases to the current active stream lease count
- **AND** `codex_lb_account_inflight_leases{account_id="acc_1",kind="response_create"}` remains the current active response-create lease count

#### Scenario: Released account leases reset gauge

- **WHEN** the proxy explicitly releases or stale-reclaims the last active `stream` lease for account `acc_1`
- **THEN** `codex_lb_account_inflight_leases{account_id="acc_1",kind="stream"}` is set to `0`

### Requirement: Streaming timeout diagnostics are emitted

For `/v1/responses` HTTP/SSE streams, the service MUST log low-cardinality diagnostics for early heartbeat emission, keepalive emission, account-capacity recovery waits, startup wait timeout, downstream disconnect, and stream idle timeout. The diagnostics MUST include request id, route family, account id when known, timeout or wait stage, model when known, bounded sleep or elapsed seconds where available, and normalized error code/message where available, without exposing payload content, API keys, raw affinity keys, or raw account emails.

#### Scenario: Keepalive path is diagnosable

- **WHEN** a streaming Responses request waits for upstream events long enough to emit keepalive data
- **THEN** the service records heartbeat or keepalive diagnostics
- **AND** the diagnostic does not include raw prompt-cache keys or request payloads

#### Scenario: Account-capacity recovery wait is diagnosable

- **WHEN** a streaming Responses request waits because account selection returned a recoverable capacity or rate-limit retry hint
- **THEN** the service logs the request id, route family, model when known, bounded wait seconds, recovery hint seconds, and normalized selection error
- **AND** the diagnostic does not include account emails, API keys, raw affinity keys, prompt text, or request payload content

#### Scenario: Local account cap wait is diagnosable

- **WHEN** a streaming request waits because local per-account stream or response-create capacity is exhausted
- **THEN** downstream keepalive and logs use the account-capacity wait path with normalized local cap reason fields
- **AND** the diagnostic does not expose prompt content, API keys, raw affinity keys, or account emails

### Requirement: HTTP bridge startup wait timeouts are logged

When an HTTP bridge startup wait times out locally, the service MUST log the request id, timeout stage, timeout seconds, and low-cardinality bridge affinity family. The log MUST NOT include raw prompt-cache keys, session ids, turn-state ids, API keys, or request payload content.

#### Scenario: Bridge startup admission timeout is diagnosable

- **WHEN** a HTTP bridge startup wait exceeds the configured proxy admission wait timeout
- **THEN** the console log includes the timeout stage and request id
- **AND** the log includes only low-cardinality affinity metadata, not raw affinity key values

### Requirement: Runtime continuity canary reports raw-error exposure and build parity
Operators MUST have a local verifier that reports whether the running `codex-lb` runtime is built from the expected code and whether recent Codex client logs contain raw `previous_response_not_found` errors.

#### Scenario: live runtime is checked after a continuity patch
- **WHEN** an operator runs the verifier on the Mac host
- **THEN** the verifier reports the repo commit, the running container image/id, local `/health` status, and recent raw `previous_response_not_found` count
- **AND** the verifier exits nonzero if raw errors are still present after the verification window
- **AND** the verifier redacts response ids by default unless `--show-ids` is passed

### Requirement: Request-log persistence failures are operator-visible
If request-log persistence fails for Responses WebSocket requests, the runtime MUST surface that condition in logs or verifier output so operators do not mistake HTTP `/health` success for continuity safety.

#### Scenario: request-log persistence fails during WebSocket traffic
- **WHEN** the runtime logs a request-log persistence failure
- **THEN** the verifier reports the failure count
- **AND** the continuity closeout cannot be marked green until persistence failures are absent or explicitly explained

### Requirement: Stale pending HTTP bridge retirement is logged

When the service retires an HTTP bridge session because pending precreated replay cannot make progress after upstream close or timeout, the service MUST emit a `retire_stale_pending` bridge event with low-cardinality bridge metadata and the terminal detail code.

#### Scenario: Failed precreated replay emits retirement event

- **WHEN** precreated HTTP bridge replay fails after upstream close or timeout
- **THEN** the console log includes a HTTP bridge event with `event=retire_stale_pending`
- **AND** the event includes only hashed bridge identity and low-cardinality metadata

### Requirement: Process-wide network recovery is observable without sensitive resolver data

The service MUST emit low-cardinality structured diagnostics when it detects a process-wide DNS or route failure, rotates shared transport state, retries a safe request, recovers, or exhausts the request budget. Diagnostics MUST NOT contain DNS server addresses, request payloads, API keys, access tokens, raw continuity keys, or account email addresses.

#### Scenario: Recovery diagnostics are emitted

- **WHEN** a safe Responses request enters and later exits process-wide network recovery
- **THEN** logs identify the recovery stage, request id, transport, attempt count, and internal account id when known
- **AND** logs do not expose resolver configuration or request content

#### Scenario: Concurrent rotation is coalesced visibly

- **WHEN** several callers report a network failure from the same shared client generation
- **THEN** diagnostics distinguish the caller that rotated the client from callers that reused the already-rotated replacement

### Requirement: Request-log metadata keeps local routing failures unbound from upstream status

When request routing fails before contacting upstream, `upstream_status_code` MUST be
`null` even if the internal failure exception carried an HTTP-like status. The
logged `upstream_error_code` MUST keep the local routing code for triage and
analytics.

#### Scenario: Additional quota or plan-routing failure is classified as local

- **WHEN** a request fails with one of `no_plan_support_for_model`,
  `additional_quota_data_unavailable`, or
  `no_additional_quota_eligible_accounts`
- **THEN** request-log metadata stores `upstream_error_code` with that exact code
- **AND** request-log metadata stores `upstream_status_code = null`

### Requirement: Request logs persist prompt-client user-agent metadata
The proxy MUST persist prompt-client user-agent metadata on `request_logs` for both HTTP and WebSocket Responses traffic. Each persisted row MUST store the full inbound `User-Agent` header value when present and a derived `useragent_group` value extracted from the first product token. When the inbound header is missing or blank after trimming, both persisted values MUST be `null`.

#### Scenario: HTTP request log stores user-agent metadata
- **WHEN** an HTTP or HTTP/SSE proxy request includes `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row stores `useragent = "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: WebSocket request log stores user-agent metadata
- **WHEN** a proxied WebSocket Responses session is opened with `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row for that request stores the full header in `useragent`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: Missing or blank user-agent remains null
- **WHEN** a proxied HTTP or WebSocket request omits the `User-Agent` header or sends only blank whitespace
- **THEN** the persisted `request_logs` row stores `useragent = null`
- **AND** the persisted row stores `useragent_group = null`

### Requirement: Supported harnesses provide observational conversation metadata

The proxy request-log metadata helper MUST detect a conversation ID only for
the first matching user-agent rule in this ordered table:

- `opencode` uses `x-parent-session-id`, then `x-opencode-session`, then
  `x-session-id`, then `x-session-affinity`.
- `codex` uses `thread-id`.

User-agent prefix matching MUST ignore surrounding whitespace and case. Header
name matching MUST be case-insensitive. The helper MUST use the first configured
header whose value is non-empty after trimming surrounding whitespace, and MUST
preserve the remaining conversation ID exactly. Detection MUST NOT reject,
rewrite, route, or otherwise alter the proxied request.

#### Scenario: Codex uses thread-id

- **GIVEN** a request has user-agent `codex/1.2` and `thread-id: " conv-a "`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `conv-a`

#### Scenario: OpenCode uses ordered fallback headers

- **GIVEN** a request has user-agent `opencode/1.0`, an empty
  `x-parent-session-id`, an empty `x-opencode-session`, `x-session-id: fallback`,
  and `x-session-affinity: affinity`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `fallback`

#### Scenario: OpenCode parent session takes precedence

- **GIVEN** a request has user-agent `opencode/1.0`,
  `x-parent-session-id: parent`, `x-opencode-session: child`,
  `x-session-id: fallback`, and `x-session-affinity: affinity`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `parent`

#### Scenario: Prefix and header matching ignore case

- **GIVEN** a request has user-agent ` CODEX/1.2 ` and header `Thread-Id:
  conv-b`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `conv-b`

#### Scenario: Unsupported harnesses produce null metadata

- **GIVEN** a request has no user-agent or has an unsupported user-agent and
  includes a configured conversation header
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is null
- **AND** the request continues through the proxy unchanged

### Requirement: Conversation metadata is nullable and indexed in request logs

The request-log persistence model MUST store `conversation_id` as a nullable
string and MUST provide an index named `idx_logs_conversation_id`. Existing rows
MUST remain valid with a null conversation ID. Empty or whitespace-only detected
values MUST be persisted as null.

#### Scenario: Known conversation ID is persisted

- **GIVEN** request-log metadata contains a non-empty conversation ID
- **WHEN** the request log is persisted
- **THEN** the stored `conversation_id` equals the trimmed ID

#### Scenario: Missing conversation ID remains nullable

- **GIVEN** request-log metadata contains no usable conversation ID
- **WHEN** the request log is persisted
- **THEN** the stored `conversation_id` is null

### Requirement: Conversation metadata propagates through every request-log path

The proxy MUST carry the detected nullable `conversation_id` into the shared
request-log persistence sink for HTTP and WebSocket requests, including normal
requests and preflight errors, and the compact, control, transcription, file,
warmup, thread-goal, and model-source paths. WebSocket finalization and HTTP
logging MUST preserve the same value derived from the inbound request headers.

#### Scenario: Normal HTTP logs retain the inbound conversation

- **GIVEN** a supported Codex or OpenCode request reaches the normal HTTP
  request-log path with a usable conversation header
- **WHEN** the path writes or finalizes its request log
- **THEN** the persisted log contains that conversation ID

#### Scenario: WebSocket logs retain the inbound conversation

- **GIVEN** a supported request reaches the WebSocket request-log path with a
  usable conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Preflight errors retain the inbound conversation

- **GIVEN** a supported request reaches the HTTP preflight-error log path with
  a usable conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Compact logs retain the inbound conversation

- **GIVEN** a supported request reaches the compact log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Control logs retain the inbound conversation

- **GIVEN** a supported request reaches the control log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Transcription logs retain the inbound conversation

- **GIVEN** a supported request reaches the transcription log path with a
  usable conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: File logs retain the inbound conversation

- **GIVEN** a supported request reaches the file log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Warmup logs retain the inbound conversation

- **GIVEN** a supported request reaches the warmup log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Thread-goal logs retain the inbound conversation

- **GIVEN** a supported request reaches the thread-goal log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Model-source logs retain the inbound conversation

- **GIVEN** a model-source request has a supported conversation header
- **WHEN** the model-source path persists its request log
- **THEN** the persisted log contains the detected conversation ID

### Requirement: Request logs persist client IP for Responses traffic

The proxy MUST persist the resolved edge client IP on `request_logs.client_ip` for HTTP, SSE, and WebSocket Responses request-log rows when a client IP is available. The proxy MUST resolve the value using the existing trusted-proxy policy, including configured trusted proxy CIDRs and supported forwarded-client-IP headers. When no client IP is available, the persisted value MUST be `null`.

#### Scenario: Direct Responses request stores socket client IP

- **WHEN** a Responses request reaches the proxy without trusted forwarded-client-IP headers
- **THEN** the persisted `request_logs` row stores the socket client IP in `client_ip`

#### Scenario: Trusted proxy request stores forwarded client IP

- **WHEN** a Responses request reaches the proxy from a trusted proxy source with a valid forwarded-client-IP header
- **THEN** the persisted `request_logs` row stores the resolved forwarded client IP in `client_ip`

#### Scenario: HTTP bridge owner logs original client IP

- **WHEN** an origin instance forwards a Responses request to an HTTP bridge owner instance
- **THEN** the owner-side request log stores the client IP resolved by the origin instance

#### Scenario: HTTP bridge replay archives under the retried request

- **WHEN** an HTTP bridge request is retried with a new request-log row and archive id
- **AND** the ambient request id still references the old session request
- **THEN** the upstream request payload is archived under the retried request's archive id

### Requirement: Request-log search matches client IP

Request-log search MUST match persisted `client_ip` values.

#### Scenario: Search by client IP returns matching rows

- **WHEN** a request log row has `client_ip = "203.0.113.7"`
- **AND** the operator searches request logs for `203.0.113.7`
- **THEN** the matching request log row is returned

### Requirement: Drain status exposes HTTP bridge activity

The internal `/internal/drain/status` payload MUST include bounded HTTP bridge
activity counters when the proxy service exposes bridge activity. The snapshot
MUST be non-blocking and MUST include whether HTTP bridge work is active, the
number of visible pending or queued bridge requests, the number of live bridge
sessions, the number of in-flight bridge session creations, the oldest in-flight
creation age in seconds, how many in-flight create markers are older than the
stale threshold, and how many completed in-flight create markers were cleaned
while building the snapshot. The HTTP bridge background cleanup task count MUST
include only active HTTP bridge close/cleanup tasks, not unrelated work stored in
shared background task registries.

#### Scenario: Drain status reports bridge work

- **WHEN** `/internal/drain/status` is requested while the proxy service has
  HTTP bridge sessions, queued work, or in-flight bridge session creation
- **THEN** the response includes HTTP bridge activity counters
- **AND** `http_bridge_active` is true when any pending, queued, session, or
  in-flight create count is non-zero

#### Scenario: Completed in-flight bridge creates are cleaned from drain status

- **WHEN** an in-flight HTTP bridge session creation marker is completed,
- **AND** `/internal/drain/status` builds the bridge activity snapshot
- **THEN** the completed marker is removed from the local in-flight create map
- **AND** the payload reports the cleaned marker count without
  blocking the health request

#### Scenario: Live stale-age bridge creates are reported but not expired

- **WHEN** an in-flight HTTP bridge session creation marker is older than the
  stale in-flight threshold but has not completed
- **AND** `/internal/drain/status` builds the bridge activity snapshot
- **THEN** the marker remains in the local in-flight create map
- **AND** the payload reports the stale marker count without completing the
  live session creation future

### Requirement: TTFT phase timings are persisted and exported
The proxy MUST persist nullable low-cardinality request-log fields for TTFT phase analysis and MUST export equivalent Prometheus phase latency observations without labels containing raw API keys, raw session ids, raw affinity keys, request ids, or prompt text.

#### Scenario: HTTP bridge request records phase timing
- **WHEN** a visible HTTP bridge request waits for session response-create admission and then receives upstream `response.created`
- **THEN** the request log includes integer millisecond timing for response-create gate wait and upstream response-created latency
- **AND** Prometheus observes phase latency with only stable labels such as phase, transport, upstream transport, and model class

#### Scenario: First upstream event is distinct from first token
- **WHEN** the upstream bridge reader receives an upstream event before text delta output
- **THEN** the request log can record first upstream event latency separately from first downstream token latency

### Requirement: Codex prewarm canary outcomes are observable

The proxy MUST record visible-request prewarm status and latency using
stable strings, and MUST emit a prewarm outcome counter labelled only by
outcome. Prewarm eligibility is the prewarm enabled flag alone: no
deterministic canary sampling or allow/deny cohort exists, so no canary
bucket or eligibility cohort dimension is recorded and the
`prewarm_status=canary_miss` value MUST NOT occur.

#### Scenario: Prewarm outcome is visible without raw identifiers

- **WHEN** Codex prewarm is enabled and a visible request triggers or skips
  a session prewarm
- **THEN** the visible request log records `prewarm_status` (and prewarm
  latency when a prewarm was attempted)
- **AND** metrics increment the outcome-labelled prewarm counter
- **AND** logs and metrics do not include raw API keys, raw session ids,
  prompt text, or affinity key values

#### Scenario: Canary sampling no longer excludes eligible requests

- **WHEN** Codex prewarm is enabled
- **THEN** no request is excluded by deterministic canary sampling
- **AND** `prewarm_status=canary_miss` is never recorded
- **AND** the prewarm counter and request log carry no canary bucket or
  eligibility cohort dimension (the legacy request-log columns remain
  unwritten for one release for rolling-upgrade safety, then are dropped)

### Requirement: 24-hour TTFT breakdown queries are available

Operators MUST have an OpenSpec context runbook or dashboard artifact with
24-hour TTFT breakdown queries by user agent group, upstream transport,
model/cache ratio, session gap cohort, prompt size cohort, and prewarm
status/outcome.

#### Scenario: Operator investigates TTFT regression

- **WHEN** an operator needs to inspect the last 24 hours of request-log
  latency
- **THEN** the repository provides SQL that reports p50, p90, p95 TTFT and
  total latency for the requested breakdowns

### Requirement: Dashboard request logs show generation speed

The dashboard request-log table MUST show time to first token and output-token generation speed when the required latency and output-token fields are available. Generation speed MUST use output tokens divided by elapsed generation time after time to first token, not total input plus output tokens and not total request latency including TTFT.

#### Scenario: TPS excludes TTFT and input tokens

- **GIVEN** a successful request log has 1,000 input tokens, 200 output tokens, 1,000 ms total latency, and 200 ms TTFT
- **WHEN** the dashboard renders request logs
- **THEN** it shows TTFT as 200ms
- **AND** it shows TPS as 250.0

#### Scenario: missing speed inputs stay blank

- **GIVEN** a request log is missing TTFT, total latency, or output tokens
- **WHEN** the dashboard renders request logs
- **THEN** it does not show a misleading calculated TPS value

### Requirement: Reports show daily median generation speed trends

The Reports dashboard MUST expose daily median TTFT, daily median TPS, and daily median queue-wait trends when request-log latency fields are available. Empty days and rows with no valid timing/speed inputs MUST render as zero in those trend charts. Daily TPS MUST median per-request output-token TPS after TTFT rather than use input tokens or include TTFT wait time. Daily queue wait MUST median per-request `latency_queue_ms` over rows where it is non-null.

#### Scenario: Daily speed charts use median valid request values

- **GIVEN** one report day has request logs with TTFT and output-token TPS values
- **WHEN** the dashboard renders Reports
- **THEN** it shows a Time to First Token chart using median TTFT for the day
- **AND** it shows a Tokens per Second chart using median per-request TPS for the day

#### Scenario: Missing daily speed data is zero-filled

- **GIVEN** a selected report range includes a day with no request logs or no valid timing data
- **WHEN** the dashboard renders Reports
- **THEN** the TTFT and TPS charts include that day with value zero

#### Scenario: Daily queue-wait trend surfaces load-balancer wait

- **GIVEN** a report day has request logs with non-null `latency_queue_ms`
- **WHEN** the dashboard renders Reports
- **THEN** it shows a queue-wait trend using the day's median `latency_queue_ms`
- **AND** days without queue samples render as zero

### Requirement: Websocket responses capture request-log latency timings

The websocket responses proxy path MUST record first-upstream-event, response-created, and first-token latency into the same request-log latency fields the HTTP bridge populates, so websocket request logs expose TTFT and generation speed. Recording MUST NOT change routing, failover, or the bytes returned to the client.

#### Scenario: Websocket request log records latency timings

- **GIVEN** a websocket responses request whose upstream emits a `response.created` event, then a text delta, then completion
- **WHEN** the proxy persists the request log
- **THEN** the log has non-null first-upstream-event, response-created, and first-token latency values
- **AND** first-upstream-event latency is less than or equal to response-created latency, which is less than or equal to first-token latency

### Requirement: Startup probe timeouts do not emit shielded-future diagnostics

The system SHALL, when the streaming proxy's startup probe times out waiting for
the first upstream event and the probed task later fails with an upstream error,
deliver that error through the streamed response without emitting an
`exception in shielded future` or `exception was never retrieved` diagnostic to
the asyncio loop exception handler.

#### Scenario: Timed-out probe whose upstream later returns 429

- **GIVEN** the startup probe times out before the first upstream event arrives
- **WHEN** the probed task subsequently fails with a 429 from the admission gate
- **THEN** the upstream error is surfaced to the caller through the streamed response
- **AND** no `exception in shielded future` diagnostic is logged

#### Scenario: Probe stream dropped before the first item is consumed

- **GIVEN** the startup probe times out and hands the running task to the response
- **WHEN** the wrapping stream is dropped before the task is awaited
- **THEN** the probed task's failure does not log an `exception was never retrieved` warning

### Requirement: Request observability distinguishes accounts from model sources

Request logs and structured diagnostics for proxied requests SHALL distinguish
subscription account routing from OpenAI-compatible model-source routing. For
source-routed requests, observability MUST include a stable source id and source
kind. For subscription-routed requests, existing account id observability MUST be
preserved. Logs and request-log payloads MUST NOT include upstream source API key
material.

#### Scenario: Source-routed request records source metadata

- **WHEN** a `/v1/chat/completions` or `/v1/audio/transcriptions` request is
  routed to OpenAI-compatible source `src_local`
- **THEN** the request log or equivalent structured diagnostic records source
  kind `openai_compatible` and source id `src_local`
- **AND** `account_id` remains null unless a subscription account was actually
  used

#### Scenario: Source API key is redacted

- **WHEN** a source-routed request is logged or archived
- **THEN** the configured upstream API key is not emitted in logs, request logs,
  metrics, diagnostics, or archive metadata

### Requirement: Request-log persistence is detached from the response path

Request-log rows MUST be persisted by tracked background tasks that the response/stream close does not wait for; persistence failures MUST be logged, and graceful shutdown MUST drain pending log writes up to the configured drain timeout so final requests' logs are not lost.

#### Scenario: Stream close does not wait for the log INSERT

- **GIVEN** a completed stream whose log INSERT is still pending
- **WHEN** the client observes the stream close
- **THEN** the row is not yet required to exist
- **AND** draining the persistence tasks then persists it exactly once

#### Scenario: Shutdown flushes pending log writes

- **WHEN** the service shuts down gracefully with log writes in flight
- **THEN** shutdown waits for them up to the configured drain timeout and reports tasks that failed to drain

### Requirement: Request speed timings share one anchor and expose queue wait

For a single request-log row, `latency_ms` and `latency_first_token_ms` MUST be
measured from the same anchor: the start of the attempt that produced the row.
Time spent before that attempt — account selection, admission waits, and failed
failover attempts — MUST NOT inflate `latency_first_token_ms`; the HTTP
streaming path MUST record it instead in a nullable `latency_queue_ms`
request-log column. First-token detection MUST treat the first output delta of
any kind — visible text, refusal, or reasoning deltas — as the first token, so
TTFT means time to first model output and the generation window
(`latency_ms - latency_first_token_ms`) covers reasoning generation, matching
the reasoning-inclusive `output_tokens` numerator used for TPS.

#### Scenario: Failover no longer inflates TTFT

- **GIVEN** a streaming request fails over from one account and succeeds on the
  next attempt
- **WHEN** the request log is persisted
- **THEN** `latency_first_token_ms` reflects only the successful attempt
- **AND** `latency_queue_ms` records the pre-attempt time (selection plus the
  failed attempt)
- **AND** `latency_ms` is greater than or equal to `latency_first_token_ms`

#### Scenario: Reasoning delta counts as the first token

- **GIVEN** an upstream stream emits a reasoning summary delta before the first
  visible text delta
- **WHEN** first-token latency is captured
- **THEN** `latency_first_token_ms` anchors to the reasoning delta rather than
  waiting for visible text

#### Scenario: Single-anchor rows on websocket and bridge paths

- **WHEN** a websocket or HTTP bridge request records latency timings
- **THEN** `latency_ms` and `latency_first_token_ms` derive from the same
  request-state anchor
- **AND** `latency_queue_ms` MAY be null on paths whose queue waits are already
  recorded in dedicated phase columns

### Requirement: Cap partition replica count is observable

The service MUST expose a Prometheus gauge named `codex_lb_cap_partition_replicas` whose value equals the live replica count currently used for account cap partitioning, and it MUST log adopted partition rebalances at info level with the old count, the new count, and this replica's rank. The gauge and log MUST NOT include account ids, instance secrets, or request payload content.

#### Scenario: Partition rebalance updates the gauge

- **GIVEN** a replica whose adopted partition has replica count 1
- **WHEN** a partition refresh observes and adopts two active members
- **THEN** `codex_lb_cap_partition_replicas` reports 2
- **AND** an info-level log records the rebalance from count 1 to count 2 with the replica's rank
