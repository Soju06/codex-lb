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

The proxy MUST provide an opt-in durable archive of Codex-to-upstream conversation traffic. The archive MUST be enabled by the dashboard setting `conversation_archive_enabled` (the `dashboard_settings` column of that name): a NULL column MUST inherit the deprecated `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED` environment variable and then the code default (off), a non-NULL column MUST win over both, and the effective value and its provenance MUST be reported by the settings API through the single `configuration-tiers` resolver. The archive writer MUST resolve the toggle at its single `archive_enabled()` gate from the last loaded dashboard-settings snapshot (`SettingsCache.cached_row()`, the environment layer before the first load); it MUST NOT read the database or await for it, and the `archive_*` call sites in the upstream HTTP and WebSocket clients MUST NOT resolve the toggle themselves. Once a snapshot has been loaded, invalidating the settings cache MUST NOT return the gate to the environment layer: the last loaded row keeps deciding until a newer one replaces it. The settings cache MUST be refreshed from the cache-invalidation bus rather than only expired, so a dashboard change reaches every replica — including one that is carrying nothing but already-open streams and therefore never pulls a snapshot in on a request — within the invalidation poll interval and without a restart. When enabled, the archive MUST write gzip-compressed newline-delimited JSON records for upstream request payloads, streamed Responses events, compact response payloads, and websocket text or binary frames without performing gzip file I/O in the request event loop during normal operation. The archive writer queue MUST be bounded and MUST apply synchronous write backpressure instead of growing without limit when the background writer is saturated. Archive records MUST include request id, timestamp, direction, traffic kind, transport, account id when known, upstream target metadata, redacted headers, and the full payload or frame body. Credential-bearing headers such as authorization, cookies, proxy authorization, token headers, and API key headers MUST be redacted before persistence. JSON records MUST preserve non-ASCII payload text as UTF-8 rather than Unicode escape sequences. When disabled, no archive file MUST be created by the archive writer. The archive directory (`CODEX_LB_CONVERSATION_ARCHIVE_DIR`) remains environment-only: each replica writes its own local shard, and the dashboard MUST show the directory read-only with that limitation stated. The settings API MUST report the directory to admin principals only and MUST NOT accept it as a write. Admin request-log API rows MUST expose an `archiveRequestId` lookup key when the persisted log id can differ from the archive record request id; guest rows MUST redact that key.

#### Scenario: operator enables archive for audit

- **WHEN** an operator confirms enabling the conversation archive in the dashboard (or `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED=true` is set while the dashboard value is unset)
- **AND** a Codex Responses request is proxied upstream after the settings cache has loaded the new snapshot
- **THEN** the archive records both the outbound upstream payload and inbound upstream events or response body as gzip JSONL
- **AND** credential-bearing headers are stored as redacted values
- **AND** no replica was restarted

#### Scenario: operator disables archive without a restart

- **GIVEN** the archive is enabled from the dashboard and records have been written
- **WHEN** the operator turns the archive off (or clears the dashboard value while the environment variable is unset)
- **AND** a later request is proxied upstream after the settings cache reloaded
- **THEN** the archive writer records nothing for that request
- **AND** existing archive files are left untouched

#### Scenario: archive remains disabled by default

- **WHEN** the archive setting is not enabled
- **THEN** the archive writer does not create conversation archive files

#### Scenario: an unrelated settings change does not resume recording

- **GIVEN** `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED=true` and an operator has turned the archive off in the dashboard
- **WHEN** any settings update invalidates the settings cache and no request has reloaded a snapshot yet
- **THEN** the archive writer still records nothing

#### Scenario: a replica carrying only open streams follows a peer's change

- **GIVEN** the archive is on and a replica is relaying an already-open stream and serving no new requests
- **WHEN** an operator turns the archive off on another replica
- **THEN** that replica refreshes its settings snapshot from the cache-invalidation bus
- **AND** later frames of the open stream are not archived

#### Scenario: dashboard value wins over the environment variable

- **GIVEN** `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED=true` and an operator has set the archive off in the dashboard
- **WHEN** a request is proxied upstream
- **THEN** the archive writer records nothing
- **AND** the settings API reports `source: "dashboard"` for `conversation_archive_enabled` and warns at startup that the environment variable is shadowed

#### Scenario: operator views archived traffic

- **GIVEN** conversation archive files exist as `.jsonl.gz` or legacy `.jsonl`
- **WHEN** an authenticated dashboard admin opens an existing request log detail
- **THEN** the dashboard can find matching archive records by request id across archive files and display payload plus metadata for that request

#### Scenario: response-id request logs keep archive lookup

- **WHEN** a successful proxied request stores a downstream response id in the request-log `requestId`
- **AND** the conversation archive stored records under the original request context id
- **THEN** the admin request-log API response includes `archiveRequestId` with the original archive lookup id
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

- **WHEN** a HTTP bridge startup wait exceeds the fixed 10-second proxy admission wait timeout
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
The proxy MUST persist prompt-client user-agent metadata on `request_logs` for both HTTP and WebSocket Responses traffic. Each persisted row MUST store the full inbound `User-Agent` header value when present and a derived `useragent_group` value. When the inbound header contains `/`, `useragent_group` MUST be the complete sequence of characters before its first `/`; when it contains no `/`, the existing group extraction behavior MUST remain unchanged. When the inbound header is missing or blank after trimming, both persisted values MUST be `null`.

#### Scenario: Historical request-log user-agent families are backfilled without normalization
- **WHEN** the user-agent family migration processes historical `request_logs` rows
- **THEN** rows whose `useragent` is non-null and contains `/` MUST have `useragent_group` set to the exact unprocessed full prefix before the first `/`
- **AND** rows whose `useragent` is `null` or contains no `/` MUST remain unchanged

#### Scenario: HTTP request log stores user-agent metadata
- **WHEN** an HTTP or HTTP/SSE proxy request includes `User-Agent: opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14`
- **THEN** the persisted `request_logs` row stores `useragent = "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"`
- **AND** the persisted row stores `useragent_group = "opencode"`

#### Scenario: Multi-word product family retains its full prefix
- **WHEN** an HTTP or HTTP/SSE proxy request includes `User-Agent: Codex Desktop/0.142.4`
- **THEN** the persisted `request_logs` row stores `useragent_group = "Codex Desktop"`

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
rewrite, route, or otherwise alter the proxied request. If
`x-parent-session-id` is blank, detection MUST fall through to the next
configured header rather than producing a null conversation ID.

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

Request-log search MUST match persisted `client_ip` values for an admin principal. For a guest principal, request-log search MUST NOT inspect or match persisted `client_ip` values.

#### Scenario: Search by client IP returns matching rows
- **WHEN** a request log row has `client_ip = "203.0.113.7"`
- **AND** an admin principal searches request logs for `203.0.113.7`
- **THEN** the matching request log row is returned

#### Scenario: Guest cannot search by redacted client IP
- **GIVEN** a request log row has `client_ip = "203.0.113.7"` and no non-sensitive field matching `203.0.113`
- **WHEN** a guest principal searches request logs for `203.0.113`
- **THEN** the request log row is not returned

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
`prewarm_status=canary_miss` value MUST NOT occur. The request-log ORM model
MUST NOT map the legacy canary bucket or eligibility cohort columns. The
physical columns MAY remain in the schema, allow-listed by the schema-drift
gate, until the release after the last release whose ORM mapped them; they
MUST NOT be dropped while a supported previous-release replica still maps
them, because that replica renders explicit NULLs for them in every
request-log INSERT while the migration Job runs ahead of the workload roll.

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
  eligibility cohort dimension

#### Scenario: Legacy canary columns stay insertable during the rolling upgrade

- **GIVEN** a database at the current Alembic head
- **WHEN** a replica running the previous release inserts a request log with
  explicit NULL `prewarm_canary_bucket` / `prewarm_eligible_reason` values
- **THEN** the insert succeeds because both physical columns still exist
- **AND** the current release's `RequestLog` model does not map either column
- **AND** the schema-drift check reports no drift for the retained columns

### Requirement: 24-hour TTFT breakdown queries are available

Operators MUST have an OpenSpec context runbook or dashboard artifact with
24-hour TTFT breakdown queries by user agent group, upstream transport,
model/cache ratio, session gap cohort, prompt size cohort, and prewarm
status/outcome.

The shipped Grafana TTFT dashboard MUST declare a visible, single-select
runtime datasource variable named `DS_SQL` that is restricted to PostgreSQL.
Every SQL panel MUST bind to the selected UID through a typed PostgreSQL
datasource object. The Helm chart MUST preserve the dashboard in its existing
sidecar-discoverable ConfigMap, and chart documentation MUST tell operators to
select the PostgreSQL datasource in Grafana.

#### Scenario: Operator investigates TTFT regression

- **WHEN** an operator needs to inspect the last 24 hours of request-log
  latency
- **THEN** the repository provides SQL that reports p50, p90, p95 TTFT and
  total latency for the requested breakdowns

#### Scenario: Sidecar-provisioned dashboard resolves the selected database

- **GIVEN** the Helm chart renders the Grafana dashboard ConfigMap
- **AND** Grafana has a PostgreSQL datasource available
- **WHEN** the operator selects that datasource through `DS_SQL`
- **THEN** all four TTFT panels resolve to the selected datasource UID
- **AND** no panel reports `Datasource ${DS_SQL} was not found`

#### Scenario: Datasource choice remains explicit and deterministic

- **WHEN** Grafana loads the TTFT dashboard
- **THEN** `DS_SQL` is visible to the operator
- **AND** it permits exactly one PostgreSQL datasource selection
- **AND** it does not offer an all-datasources selection

### Requirement: Dashboard request logs show generation speed

The dashboard request-log table MUST show time to first token and output-token generation speed when the required latency and output-token fields are available. Generation speed MUST use non-reasoning output tokens divided by elapsed generation time after time to first token, not total input plus output tokens and not total request latency including TTFT. When reasoning-token usage is unknown, it MUST be treated as zero for this metric. The displayed metric MUST remain named `TPS`.

#### Scenario: TPS excludes TTFT, input tokens, and reasoning tokens

- **GIVEN** a successful request log has 1,000 input tokens, 200 output tokens, including 40 reasoning tokens, 1,000 ms total latency, and 200 ms TTFT
- **WHEN** the dashboard renders request logs
- **THEN** it shows TTFT as 200ms
- **AND** it shows TPS as `(200 - 40) / 0.8 = 200.0`

#### Scenario: Unknown reasoning usage is treated as zero

- **GIVEN** a request has output tokens, valid total latency and TTFT, but no reasoning-token usage
- **WHEN** the dashboard calculates TPS
- **THEN** it uses the full output-token count as non-reasoning output

#### Scenario: missing speed inputs stay blank

- **GIVEN** a request log is missing TTFT, total latency, or output tokens
- **WHEN** the dashboard renders request logs
- **THEN** it does not show a misleading calculated TPS value

#### Scenario: invalid speed inputs stay blank

- **GIVEN** a request log has output tokens and latency fields
- **AND** either total latency is less than or equal to TTFT or non-reasoning output tokens are zero or negative
- **WHEN** the dashboard renders request logs
- **THEN** it does not show a calculated TPS value

### Requirement: Reports show daily median generation speed trends

The Reports dashboard MUST expose daily median TTFT, daily median TPS, and daily median queue-wait trends when request-log latency fields are available. Empty days and rows with no valid timing/speed inputs MUST render as zero in those trend charts. Daily TPS MUST median per-request non-reasoning output-token TPS after TTFT rather than use input tokens or include TTFT wait time. Unknown reasoning-token usage MUST be treated as zero when deriving non-reasoning output tokens. Daily queue wait MUST median per-request `latency_queue_ms` over rows where it is non-null.

#### Scenario: Daily speed charts use median valid request values

- **GIVEN** one report day has request logs with TTFT and output-token TPS values
- **WHEN** the dashboard renders Reports
- **THEN** it shows a Time to First Token chart using median TTFT for the day
- **AND** it shows a Tokens per Second chart using median per-request TPS for the day

#### Scenario: Invalid daily speed samples are excluded

- **GIVEN** a report day contains rows where total latency is less than or equal to TTFT or non-reasoning output tokens are zero or negative
- **WHEN** the daily TPS median is calculated
- **THEN** those rows are excluded from the median

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

The websocket responses proxy path MUST record first-upstream-event, response-created, and first-token latency into the same request-log latency fields the HTTP bridge populates, so websocket request logs expose TTFT and generation speed. First-token latency MUST use the first token-bearing output delta, including text, refusal, reasoning-summary, function-call argument, custom-tool input, and tool-call output deltas, or a custom/apply-patch tool-call `response.output_item.added` or `response.output_item.done` event only when the item contains meaningful tool-call payload content and the tool protocol does not stream argument deltas. Recording MUST NOT change routing, failover, or the bytes returned to the client.

#### Scenario: Websocket text response records latency timings
- **GIVEN** a websocket responses request whose upstream emits a `response.created` event, then a text delta, then completion
- **WHEN** the proxy persists the request log
- **THEN** the log has non-null first-upstream-event, response-created, and first-token latency values
- **AND** first-upstream-event latency is less than or equal to response-created latency, which is less than or equal to first-token latency

#### Scenario: Websocket tool call records first-token latency
- **GIVEN** a websocket responses request whose first token-bearing output is a function-call argument delta, custom-tool input delta, tool-call output delta, or a custom/apply-patch tool-call `response.output_item.added` or `response.output_item.done` event with meaningful tool-call payload content when the tool protocol does not stream argument deltas
- **WHEN** the proxy persists the request log
- **THEN** the log has a non-null first-token latency value
- **AND** the proxy forwards the upstream event unchanged

#### Scenario: Control events do not record first-token latency
- **GIVEN** a responses request whose upstream has emitted only control events such as `response.created`
- **WHEN** the proxy inspects the request timing
- **THEN** first-token latency remains null until a token-bearing output delta arrives, unless a meaningful custom/apply-patch completion event anchors TTFT for a completion-only tool protocol
- **AND** a message, reasoning, or function-call `response.output_item.added` lifecycle event does not record first-token latency
- **AND** reasoning-summary placeholder deltas that are stripped before delivery do not record first-token latency
- **AND** metadata-only or empty tool-call delta and completion events do not record first-token latency

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
streaming path MUST record it instead in a nullable `latency_queue_ms`.
First-token detection MUST treat the first token-bearing output event — visible text, refusal, reasoning deltas, function-call argument, custom-tool input, tool-call output, or a custom/apply-patch tool-call `response.output_item.added` or `response.output_item.done` event with meaningful tool-call payload content when the tool protocol does not stream argument deltas — as the first token. Lifecycle/control events, reasoning-summary placeholder deltas stripped before delivery, and metadata-only or empty tool-call deltas or completion events MUST NOT record first-token latency. TTFT means time to first model output and the generation window (`latency_ms - latency_first_token_ms`) covers reasoning generation, while TPS uses the non-reasoning output-token numerator.

#### Scenario: Failover no longer inflates TTFT

- **GIVEN** a streaming request fails over from one account and succeeds on the
  next attempt
- **WHEN** the request log is persisted
- **THEN** `latency_first_token_ms` reflects only the successful attempt
- **AND** `latency_queue_ms` records the pre-attempt time (selection plus the
  failed attempt)
- **AND** `latency_ms` is greater than or equal to `latency_first_token_ms`

#### Scenario: Non-placeholder reasoning delta counts as the first token

- **GIVEN** an upstream stream emits a non-placeholder, token-bearing reasoning summary delta before the first visible text delta
- **WHEN** first-token latency is captured
- **THEN** `latency_first_token_ms` anchors to the reasoning delta rather than waiting for visible text

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

### Requirement: Source-routed requests report upstream-measured generation timings

The proxy MUST record upstream-reported generation timing on the request log
for source-routed chat/responses/audio-transcription requests when the
OpenAI-compatible source's response body includes a `metrics` object with
`time_to_first_token_ms` and `generation_time_ms`. The proxy MUST set
`latency_first_token_ms` to the reported time-to-first-token and `latency_ms`
to the sum of time-to-first-token and generation time, using the same
request-log fields subscription-backed requests already populate. Sources
that do not return a `metrics` object MUST leave both fields `null`, and
negative or non-numeric values MUST be rejected rather than recorded.
Non-finite numeric values (`NaN`, positive infinity, or negative infinity)
MUST also be rejected rather than failing or interrupting the proxied request.

#### Scenario: Source metrics populate TTFT and total latency

- **GIVEN** an OpenAI-compatible source's chat completion response includes
  `metrics: {time_to_first_token_ms: 108.83, generation_time_ms: 162.98}`
- **WHEN** the request is logged
- **THEN** the request log's `latency_first_token_ms` is `109`
- **AND** the request log's `latency_ms` is `272`

#### Scenario: Streamed responses capture metrics from the final frame

- **GIVEN** a source-routed streaming chat completion whose final SSE frame
  carries both `usage` and `metrics`
- **WHEN** the stream completes successfully
- **THEN** the request log records the same `latency_first_token_ms` /
  `latency_ms` derived from that frame's `metrics`

#### Scenario: Missing metrics leaves latency fields null

- **GIVEN** an OpenAI-compatible source's response includes no `metrics` object
- **WHEN** the request is logged
- **THEN** `latency_ms` and `latency_first_token_ms` remain `null`, unchanged
  from prior behavior

#### Scenario: Dashboard retains generation-only throughput semantics

- **GIVEN** a source response reports `time_to_first_token_ms: 108.83`,
  `generation_time_ms: 162.98`, and `9` output tokens, including zero reasoning
  tokens
- **WHEN** the existing dashboard computes tokens per second as non-reasoning
  output tokens divided by `latency_ms - latency_first_token_ms`
- **THEN** it reports approximately `55.2` generation tokens per second
- **AND** it does not substitute an upstream `tokens_per_second` value that may
  include TTFT

#### Scenario: Non-finite metrics are ignored safely

- **GIVEN** a source response contains `NaN` or infinity in either timing field
- **WHEN** the proxy parses the optional metrics
- **THEN** both timing values remain unset
- **AND** the otherwise successful proxied request is not interrupted

### Requirement: Shipped high-error-rate alert uses aggregate request share

The shipped `CodexLBHighErrorRate` alert MUST calculate, independently for each
namespace and job, the sum of five-minute 5xx request rates divided by the sum
of all five-minute request rates. Method, path, status, instance, replica, and
other non-scope labels MUST be aggregated before division. The alert MUST
compare the aggregate ratio to 0.05 and MUST require it to remain above that
threshold for five minutes.

#### Scenario: Mixed success and error series produce their aggregate share

- **GIVEN** one namespace and job have positive 2xx and 5xx request rates
- **WHEN** the high-error-rate alert expression is evaluated
- **THEN** the ratio equals the sum of 5xx request rates divided by the sum of
  all request rates
- **AND** the ratio is not 1 unless all requests in that group are 5xx

#### Scenario: Alert groups remain isolated

- **GIVEN** request series exist for more than one namespace or job
- **WHEN** the high-error-rate alert expression is evaluated
- **THEN** each namespace and job pair has an independent aggregate ratio
- **AND** traffic from one pair is not included in another pair

#### Scenario: Threshold and duration apply to the aggregate ratio

- **GIVEN** one namespace and job have an aggregate 5xx share above 0.05
- **WHEN** that aggregate share remains above 0.05 for five minutes
- **THEN** `CodexLBHighErrorRate` fires for that namespace and job pair

### Requirement: Bundled Grafana 5xx stat uses selected aggregate request share

The bundled Grafana `Error Rate (5xx)` stat MUST apply the selected namespace
and job filters to both operands, aggregate all remaining request-series labels
before division, and display the resulting 5xx share as one value. When the
selected total request rate is positive but no matching 5xx series exists, the
stat MUST display 0%.

#### Scenario: Selected mixed traffic produces one aggregate value

- **GIVEN** the selected namespace and job have positive 2xx and 5xx request
  rates across one or more request or replica label combinations
- **WHEN** the Grafana error-rate stat is evaluated
- **THEN** it displays the sum of selected 5xx request rates divided by the sum
  of all selected request rates

#### Scenario: Dashboard selection filters both operands

- **GIVEN** request series exist inside and outside the selected namespace and
  job
- **WHEN** the Grafana error-rate stat is evaluated
- **THEN** both the 5xx numerator and total denominator exclude traffic outside
  the selected namespace and job

#### Scenario: Success-only traffic displays zero

- **GIVEN** the selected scope has a positive successful-request rate
- **AND** no matching 5xx series exists
- **WHEN** the Grafana error-rate stat is evaluated
- **THEN** the stat displays 0%

### Requirement: Stream pool congestion is observable

When Prometheus support is available the service MUST expose a gauge named `codex_lb_stream_pool_capacity` whose value equals the fair-share gate's most recently computed candidate pool capacity and a gauge named `codex_lb_stream_pool_inflight` whose value equals the corresponding pool in-flight stream count, and a counter named `codex_lb_api_key_fair_share_rejections_total` incremented once per fair-share denial. The gauges and the counter MUST NOT carry API-key, account, or request labels. Each fair-share denial MUST log at warning level with the requesting `api_key_id`, the key's in-flight count, the computed fair share, the pool in-flight and capacity, and the active-key count, and MUST NOT include other keys' identifiers, instance secrets, or request payload content. All fair-share metrics MUST degrade to no-ops when the Prometheus client is absent.

#### Scenario: Pool gauges are exported during gate evaluation

- **GIVEN** the fair-share gate is enabled and evaluates a stream selection
- **WHEN** metrics are scraped
- **THEN** `codex_lb_stream_pool_capacity` and `codex_lb_stream_pool_inflight` report the evaluated pool values without per-key or per-account labels

#### Scenario: Denials are counted without key cardinality

- **GIVEN** repeated fair-share denials for multiple keys
- **WHEN** metrics are scraped
- **THEN** `codex_lb_api_key_fair_share_rejections_total` reflects the total denial count with no per-key label

#### Scenario: Denial log carries the diagnostic numbers

- **GIVEN** a fair-share denial
- **WHEN** the warning is logged
- **THEN** it includes the requester's `api_key_id`, key in-flight count, fair share, pool in-flight, pool capacity, and active-key count and no other key's identifier

### Requirement: Event-loop scheduling lag is observable

The system MUST sample event-loop scheduling lag (timer drift of a
once-per-second sleep) while serving and export it as the
`codex_lb_event_loop_lag_seconds` gauge. Samples at or above the configured
warning threshold MUST increment `codex_lb_event_loop_lag_warnings_total` and
emit a warning log that names the observed lag, the worst lag suppressed since
the previous line, and the threshold; the warning log MUST be rate-limited so
a sustained stall cannot flood the log. The threshold MUST be configurable via
`event_loop_lag_warn_threshold_seconds` with a working default requiring no
operator action, and `0` MUST disable the watchdog.

#### Scenario: Starved event loop produces an explicit operator signal

- **WHEN** the event loop is starved (callback storm, synchronous work on the
  loop, or CPU saturation) and scheduling lag reaches the warning threshold
- **THEN** `codex_lb_event_loop_lag_warnings_total` increments
- **AND** a rate-limited `event_loop_lag` warning names the observed lag and
  threshold, distinguishing loop starvation from upstream slowness

#### Scenario: Healthy loop stays quiet

- **WHEN** scheduling lag stays below the warning threshold
- **THEN** the gauge is still updated for dashboards
- **AND** no warning is logged and the warning counter does not increment

#### Scenario: Watchdog can be disabled

- **WHEN** `event_loop_lag_warn_threshold_seconds` is set to `0`
- **THEN** the watchdog task is not started

### Requirement: Unroutable upstream bridge events are logged

An HTTP bridge session multiplexes one upstream connection across its pending requests.
When an upstream event cannot be attributed to any pending request, the service MUST log
it once with the event type, whether the event carried a response id, and the count of
pending requests on that session. The log MUST NOT include raw prompt-cache keys,
session ids, response ids, or payload content.

Terminal bookkeeping events that are expected to arrive with no pending request — the
drain and retirement paths that already run after a session's requests have been
settled — MUST NOT be logged as unroutable, so the signal stays specific to events that
were dropped while work was still waiting.

#### Scenario: Event arrives with no pending request to receive it

- **GIVEN** an HTTP bridge session with at least one pending request
- **WHEN** an upstream event matches none of those pending requests
- **THEN** the service logs the event type and the pending-request count
- **AND** the log contains no response id, prompt-cache key, or payload content

#### Scenario: Routed events stay silent

- **WHEN** an upstream event is attributed to a pending request
- **THEN** no unroutable-event log is emitted for it

### Requirement: WebSocket scope cleanup timeout identifies its blocked phase

When WebSocket scope finalization exceeds its cleanup budget, the proxy MUST
include the current cleanup phase in the existing warning. The phase MUST be a
fixed low-cardinality value that identifies the cleanup operation and MUST NOT
contain request ids, account ids, request payloads, credentials, or exception
content. This diagnostic MUST NOT change cleanup ordering, timeout budgets,
retry behavior, or task ownership.

The phase MUST be one of `not_started`, `upstream_close`, `upstream_reader`,
`retired_create_lease`, `unsent_request`, `replay_request`, `pending_requests`,
`connection_lease`, or `complete`. `not_started` is the fallback before the
first cleanup operation begins. `complete` records finished cleanup and MUST NOT
appear in a timeout warning. Missing or unrecognized phases MUST fall back to
`not_started`; implementations MUST NOT derive a phase from request or exception
data.

#### Scenario: Pending request finalization exceeds the cleanup budget

- **GIVEN** a cancelled WebSocket scope whose pending request finalization does
  not finish within the cleanup budget
- **WHEN** the proxy emits the cleanup-budget warning
- **THEN** the warning includes `cleanup_phase=pending_requests`
- **AND** the cleanup remains owned by the existing background drain

#### Scenario: Diagnostic phase remains low-cardinality

- **WHEN** any WebSocket scope cleanup phase exceeds the cleanup budget
- **THEN** the warning identifies only a fixed cleanup phase
- **AND** the phase contains no request id, account id, payload, credential, or
  exception content

### Requirement: Durable transcript cleanup exposes bounded aggregate telemetry

Each durable operation transcript cleanup pass MUST expose low-cardinality
telemetry for pass duration, deleted-operation count, completion outcome, and
whether the pass stopped with likely eligible backlog remaining. Logs and
metrics MUST NOT include prompt/output text, operation identifiers, session
keys, account identifiers, or model names.

If a later batch fails after earlier batches committed, failure telemetry MUST
include the operations deleted and batches completed before that failure.

#### Scenario: Budget exhaustion is observable

- **GIVEN** eligible transcript operations remain after a cleanup pass reaches
  its fixed budget
- **WHEN** the pass stops
- **THEN** aggregate telemetry reports the deleted count and a budget-exhausted
  outcome
- **AND** reports that backlog is likely to remain

#### Scenario: A drained pass clears the backlog signal

- **WHEN** a cleanup pass selects fewer operations than the repository batch
  size, even if ownership rechecks deleted fewer rows from an earlier full
  selection
- **THEN** aggregate telemetry reports a completed outcome
- **AND** clears the backlog-likely signal

#### Scenario: Failed pass preserves partial progress telemetry

- **GIVEN** one or more cleanup batches commit before a later batch fails
- **WHEN** failure telemetry is recorded
- **THEN** it includes the committed deletion and batch counts

#### Scenario: Cleanup telemetry contains no sensitive labels

- **WHEN** cleanup succeeds or fails
- **THEN** its logs and metric labels contain no operation, session, account,
  prompt, output, or model values

### Requirement: Bundled Grafana latency quantiles aggregate selected buckets

Every bundled Grafana request/upstream `histogram_quantile` MUST apply selected
namespace/job filters and sum five-minute bucket rates by `le` before quantile.
Residual method, path, instance, pod, replica, and scrape labels MUST NOT create
additional quantile series.

#### Scenario: Selected latency produces one series per quantile

- **GIVEN** matching buckets span methods, paths, and scrape targets
- **WHEN** bundled request p50/p95/p99 or upstream p99 evaluates
- **THEN** matching rates are summed by `le` first
- **AND** one selected-scope series remains per quantile

### Requirement: High-latency alert aggregates by operational scope

`CodexLBHighLatency` MUST calculate p99 from request buckets summed by
namespace, job, and `le`. It MUST retain the ten-second threshold and
five-minute duration.

#### Scenario: Residual labels produce one alert value per scope

- **GIVEN** one namespace/job has buckets across methods, paths, and replicas
- **WHEN** the alert evaluates
- **THEN** one aggregate p99 remains for that namespace/job

### Requirement: Operation abandonment is observable

When bridge maintenance moves an ambiguous operation to `abandoned`, the
service MUST emit a structured low-cardinality diagnostic containing the
source state, abandonment reason, bounded age, and owner-lease outcome, and
MUST increment a Prometheus counter labeled only by source state. The
diagnostic and metric MUST NOT contain request text, response IDs, API keys,
account emails, or raw continuity keys.

#### Scenario: stale operation abandonment is diagnosable

- **WHEN** an eligible `unknown` or `acknowledged` operation is abandoned
- **THEN** logs identify the source state and stale-owner reason
- **AND** the abandonment counter increases for that source state
- **AND** no sensitive request or continuity value is emitted

### Requirement: Rendered log records redact URL userinfo and keyed secrets

Every log record rendered by the application's text, access, and JSON
formatters MUST have URL userinfo, in both the `scheme://user:password@` and
the username-only `scheme://user@` forms, replaced with
`scheme://[REDACTED]@` and `Basic <token>` authorization tokens (in the
`Basic`, `basic` and `BASIC` scheme spellings)
(a reversible encoding of `user:password`, as carried in aiohttp proxy-error
reprs) replaced with `Basic [REDACTED]`, regardless of the originating logger
(application, `asyncio`, aiohttp, uvicorn) and including exception and stack
text. Structured extra keys MUST be redacted like values. Records at WARNING
level or higher MUST additionally have keyed secrets (`password=`, `token=`,
`api_key=`, bearer, basic and authorization values in any letter case, JSON
secret fields embedded in strings, and structured extra fields whose key names
a secret, whatever the value type) redacted. Redaction MUST never raise and
MUST fail closed: if a redaction pass fails, the affected text is replaced with
a `[REDACTED: log redaction failed]` placeholder and the record is still
emitted with its timestamp, level and logger; structured extras that are cyclic,
pathologically deep, or unprintable MUST still be emitted with redaction
applied to every finite, printable part. Application startup MUST route
`warnings.warn` output through the same log handlers. Log records that contain
no secret patterns MUST render byte-identically to the unredacted rendering.

#### Scenario: Unclosed aiohttp connection repr is credential-free

- **GIVEN** an aiohttp connection is finalized without release and its connection key holds a credentialed proxy URL
- **WHEN** the loop exception handler logs `Unclosed connection` through the `asyncio` logger
- **THEN** the rendered record contains `proxy=URL('scheme://[REDACTED]@host:port')`
- **AND** the password appears in neither the text nor the JSON rendering

#### Scenario: Userinfo containing unencoded sub-delims is redacted

- **GIVEN** a proxy password containing an RFC 3986 sub-delim such as `'`, which yarl leaves unencoded in the URL userinfo, or a raw environment proxy string that is not percent-encoded at all
- **WHEN** the URL is rendered in any log record at any level
- **THEN** the record contains `scheme://[REDACTED]@host:port` and neither the raw nor the percent-encoded password

#### Scenario: Username-only URL userinfo is redacted

- **GIVEN** a URL whose userinfo carries only a username (a token used as the username, `scheme://user@host:port`) with no `:password` part
- **WHEN** the URL is rendered in any log record at any level, text or JSON
- **THEN** the record contains `scheme://[REDACTED]@host:port` and the username does not appear

#### Scenario: Proxy error repr with a Basic token is masked

- **GIVEN** an aiohttp proxy error whose tunnel request headers carry `Proxy-Authorization: Basic <token>`
- **WHEN** the error is logged with `%r` at any level, or its repr is logged by the loop's exception handler for an unretrieved task
- **THEN** the rendered record contains `'Proxy-Authorization': 'Basic [REDACTED]'`
- **AND** neither the token nor the password appears in the text or JSON rendering

#### Scenario: Secret-keyed structured extras are masked

- **GIVEN** a WARNING or higher record carries an extra field such as `{"password": "..."}` or `{"access_token": "..."}`
- **WHEN** the JSON formatter renders the record
- **THEN** the field value is replaced with `[REDACTED]` whatever its type (string, list, number, bytes, mapping); a null value stays null
- **AND** fields such as `attempt` or `tokens` keep their values
- **AND** an extra key carrying URL userinfo is rendered as `scheme://[REDACTED]@host`

#### Scenario: Secret-free records are unchanged

- **WHEN** a record such as the one-time bootstrap token banner contains no URL userinfo or keyed secret pattern
- **THEN** the rendered output is byte-identical to the unredacted rendering

#### Scenario: Redaction failure never breaks logging and fails closed

- **WHEN** a redaction pass raises while rendering a record
- **THEN** the record is still emitted, in text and JSON, with its timestamp, level and logger
- **AND** the affected text is rendered as `[REDACTED: log redaction failed]` rather than the original text

#### Scenario: Cyclic or unprintable structured extras never drop the record

- **GIVEN** a record carries an extra whose container refers back to itself, or whose `repr()` raises
- **WHEN** the JSON formatter renders the record
- **THEN** the record is emitted, the back-reference collapses to a `{...}` / `[...]` placeholder and the unprintable value to an `<unprintable ...>` marker
- **AND** secret-keyed fields and URL userinfo in the finite part of the extra are still redacted

#### Scenario: Secret-keyed mappings rendered as Python repr are masked

- **GIVEN** a WARNING or higher record renders a mapping with `%r`, or the JSON formatter falls back to text for a structured extra (nesting beyond the depth limit, a container whose iteration raises, an unserializable rebuild)
- **WHEN** the rendered text contains `'password': 'x'`, `'access_token': [...]` or `'api_key': 123`
- **THEN** each secret-keyed value is replaced with `[REDACTED]` (quotes kept for quoted strings)
- **AND** `'Proxy-Authorization': 'Basic <token>'` keeps rendering as `'Basic [REDACTED]'`

### Requirement: Loop exception handler output redacts context value reprs

Application startup MUST install an asyncio loop exception handler that
redacts the `repr()` of every context value the default handler would render
(all keys except the textual `message`, `exception`, `source_traceback` and
`handle_traceback` entries) before delegating to the previously installed or
default handler. Secret-free context output MUST be byte-identical to the
default handler output, the exception object and its traceback MUST be passed
through unchanged, installation MUST be idempotent, and the handler MUST fail
closed: a context value whose `repr()` raises MUST be replaced by an opaque
stand-in, and any other failure inside the redacting handler MUST delegate a
context that keeps the textual entries and replaces every other value with an
opaque stand-in, so the report is still emitted and no unredacted value reaches
the delegate.

#### Scenario: Unclosed aiohttp connection repr is credential-free before logging

- **GIVEN** an aiohttp connection is finalized without release and its connection key holds a credentialed proxy URL
- **WHEN** the loop exception handler receives `Unclosed connection` with the connection object in its context
- **THEN** the `asyncio` log record already contains `proxy=URL('scheme://[REDACTED]@host:port')`
- **AND** the password appears nowhere in the record, independent of the formatter in use, including passwords carrying sub-delims such as `'` that yarl leaves unencoded

#### Scenario: Unretrieved task exception repr is redacted

- **WHEN** a task whose exception text or repr carries URL userinfo or a `Basic <token>` header is garbage-collected unretrieved
- **THEN** the `Task exception was never retrieved` record renders the task repr with `[REDACTED]` in place of the credential

#### Scenario: Secret-free contexts and failures are transparent

- **WHEN** the context contains no secret pattern
- **THEN** the emitted record message and exception info are byte-identical to the default handler output
- **WHEN** a handler was already installed
- **THEN** the previously installed handler still receives the (redacted) context
- **WHEN** a context value's `repr()` raises
- **THEN** the record is still emitted with its `message` line intact and that value rendered as an opaque stand-in naming the value type and the exception type
- **WHEN** the redaction pass itself fails
- **THEN** the record is still emitted with its `message`, exception and traceback entries intact and every other value rendered as an opaque redaction-failed stand-in

### Requirement: Request metric labels have bounded cardinality

The service MUST expose request counter and duration metrics with finite-vocabulary
`method` and `path` labels. The `method` label MUST be one of `GET`, `POST`, `PUT`,
`PATCH`, `DELETE`, `HEAD`, `OPTIONS`, or `OTHER`; any other HTTP method MUST map to
`OTHER`. The `path` label MUST preserve the existing `/v1/...`, `/api/...`, and
`/health/...` collapse values and the existing bare `/health` value. Paths under
`/backend-api/` MUST map to `/backend-api/...`, and paths under `/internal/` MUST map
to `/internal/...`; every other path MUST map to the single `/other` sentinel. Metric
labels MUST NOT contain raw or truncated unmatched paths.

#### Scenario: Unmatched paths share one metric label

- **WHEN** requests use distinct paths outside the `/v1/`, `/api/`, `/health/`,
  `/backend-api/`, and `/internal/` prefixes, including SPA-looking paths
- **THEN** request counter and duration metrics use `path="/other"` for every
  such request
- **AND** no raw unmatched path or truncated unmatched path becomes a metric
  label value

#### Scenario: Primary proxy paths use bounded labels

- **WHEN** requests use `/backend-api/codex/responses`, dynamic
  `/backend-api/files/{file_id}/uploaded`, or `/internal/bridge/...` paths
- **THEN** request counter and duration metrics use `/backend-api/...` for every
  `/backend-api/` path and `/internal/...` for every `/internal/` path
- **AND** no dynamic file ID or other raw suffix becomes a metric label value

#### Scenario: Unsupported methods share the OTHER label

- **WHEN** a request uses an HTTP method outside the supported method vocabulary
- **THEN** request counter and duration metrics use `method="OTHER"`

#### Scenario: Existing collapsed paths remain stable

- **WHEN** a request uses a path under `/v1/`, `/api/`, or `/health/`, or uses bare `/health`
- **THEN** the metric path label retains its existing value

### Requirement: Secret-pattern redaction stays on the current line

Keyed, bearer, basic, authorization, JSON, and Python-repr secret patterns
MUST be applied independently to each CR/LF-delimited line of rendered log
text. A match MUST NOT consume CR or LF or any text from a following line.
Unterminated JSON secret values MUST be redacted through the end of the
current line. A Bearer credential MUST treat a glued `:` tail on the same
line as credential material. Same-line comma and ampersand separators MUST
keep their existing truncation behavior. Records below WARNING MUST still
skip these keyed patterns.

#### Scenario: Authorization does not swallow the next traceback line

- **GIVEN** a WARNING or higher record whose exception text contains
  `authorization=Basic X` followed by a newline and `status=failed`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the Basic credential is replaced with `[REDACTED]`
- **AND** the following line still contains `status=failed`

#### Scenario: Unterminated JSON secret is redacted through end of line

- **GIVEN** a WARNING or higher record contains `{"token":"abc` with no
  closing quote before the line ending, then a following `safe diagnostic line`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the token value is replaced with `[REDACTED]`
- **AND** `safe diagnostic line` remains

#### Scenario: Bearer glued colon tail is redacted

- **GIVEN** a WARNING or higher record contains
  `Bearer abc.def:GLUEDTAIL, status=502`
- **WHEN** the text or JSON formatter renders the record
- **THEN** the rendered text contains `Bearer [REDACTED], status=502`
- **AND** neither `abc.def` nor `GLUEDTAIL` appears

#### Scenario: Same-line authorization truncation is unchanged

- **GIVEN** a WARNING or higher record contains
  `Authorization: Basic dXNlcjpwYXNz status=failed` on one line with no comma
- **WHEN** the text formatter renders the record
- **THEN** the credential is redacted
- **AND** `status=failed` is not present on that line

#### Scenario: Line terminators are preserved and redaction is idempotent

- **GIVEN** rendered secret-bearing text that uses LF and CRLF separators
- **WHEN** secret-pattern redaction is applied once and then again
- **THEN** the terminator bytes and line count are unchanged
- **AND** the second pass equals the first

### Requirement: Upstream reasoning-replay rejections are counted

When Prometheus support is available the proxy MUST expose a label-free counter
named `codex_lb_upstream_reasoning_replay_400_total` and MUST increment it exactly
once per upstream stream failure that is an HTTP 400 rejection, or a terminal
`error` / `response.failed` frame carrying `invalid_request_error` without an HTTP
status, whose error message references reasoning. Frames MUST be counted where
the terminal frame is classified -- on the SSE streaming path, the websocket
path, and the HTTP bridge (which finalizes through the websocket path) --
independent of whether an account-health write follows, because
`invalid_request_error` is never penalized and therefore never reaches the
account-health handler. Counting MUST NOT alter failure classification, account
health, or failover, MUST NOT log the rejection message body, and MUST degrade to
a no-op when the Prometheus client is absent.

#### Scenario: Reasoning replay rejection is counted

- **WHEN** upstream rejects a stream with HTTP 400 and a message such as `Item with id 'rs_...' of type 'reasoning' was provided without its required following item.`
- **THEN** `codex_lb_upstream_reasoning_replay_400_total` increments by one
- **AND** the failure is classified and penalized exactly as before

#### Scenario: Terminal frames are counted without an account-health write

- **WHEN** an upstream SSE stream, websocket session, or HTTP-bridge session ends with a terminal `error` or `response.failed` frame whose code is `invalid_request_error` and whose message references reasoning
- **THEN** `codex_lb_upstream_reasoning_replay_400_total` increments by exactly one
- **AND** the frame is neither penalized nor otherwise classified differently than before

#### Scenario: Other rejections are not counted

- **WHEN** upstream rejects a stream with HTTP 400 without referencing reasoning, with a non-400 status whose message mentions reasoning, or with a terminal frame whose code is not `invalid_request_error`
- **THEN** the counter does not change

#### Scenario: Missing Prometheus client

- **WHEN** the Prometheus client is not installed
- **THEN** counting is a no-op and stream error handling is unchanged

### Requirement: Timeout-invariant violations are diagnosable

Timeout-invariant validation diagnostics SHALL include the rule id, left-hand
setting or expression and value, relation, right-hand setting or expression and
value, rationale, and code anchors. Diagnostics SHALL avoid request payloads,
API keys, access tokens, raw affinity keys, account emails, and other
high-cardinality runtime identifiers. Diagnostics SHALL describe startup
validation of `Settings` and imported constants only, not per-request overrides,
runtime clamps, or runtime-derived effective values.

#### Scenario: Violation log names the invariant

- **WHEN** startup timeout-invariant validation observes a violated rule
- **THEN** the CRITICAL log includes that rule id and rationale
- **AND** the log contains no request payload, API key, access token, raw
  affinity key, or account email

### Requirement: Guest request logs redact raw identifying metadata

The dashboard request-log API MUST return request rows and non-identifying operational metrics to a guest principal, but MUST serialize `clientIp`, full `useragent`, `conversationId`, and `archiveRequestId` as null and MUST NOT use those redacted values to match guest text searches. It MUST reject a guest request that supplies the dedicated `conversation_id` filter with HTTP 403 and error code `admin_access_required`. It MUST retain the identifying values and existing conversation filtering and aggregate response for an admin principal. The lower-cardinality `useragentGroup`, status, model, token, latency, and cost fields MAY remain available to guests outside a dedicated conversation filter.

#### Scenario: Guest reads operational request rows without raw identifiers

- **GIVEN** a persisted request log contains a client IP, full User-Agent, conversation ID, archive lookup ID, model, status, tokens, latency, and cost
- **WHEN** a guest principal requests `GET /api/request-logs`
- **THEN** the response row has null `clientIp`, `useragent`, `conversationId`, and `archiveRequestId`
- **AND** the response retains the row's non-identifying operational fields

#### Scenario: Admin retains raw request metadata

- **GIVEN** a persisted request log contains a client IP, full User-Agent, conversation ID, and archive lookup ID
- **WHEN** an admin principal requests `GET /api/request-logs`
- **THEN** the response contains the persisted values

#### Scenario: Guest conversation filter fails closed

- **GIVEN** persisted request logs contain a redacted conversation ID
- **WHEN** a guest principal requests `GET /api/request-logs` with that `conversation_id`
- **THEN** the response is HTTP 403 with error code `admin_access_required`
- **AND** no filtered row count or aggregated conversation cost is returned

#### Scenario: Admin retains conversation filtering and aggregates

- **GIVEN** persisted request logs contain a conversation ID
- **WHEN** an admin principal requests `GET /api/request-logs` with that `conversation_id`
- **THEN** only matching request rows are returned
- **AND** the response retains the matching request count and aggregated conversation cost

### Requirement: Durable affinity observation on request logs

The system MUST persist `sticky_key_source`, `sticky_kind`, and `sticky_key_hash` on existing Responses and compact request-log rows independently of trace settings. Source MUST describe the existing resolved source classification. Kind and hash MUST describe the resolved affinity policy associated with the logged attempt or final request state. The hash MUST be the first 16 lowercase hexadecimal characters of SHA-256 over the resolved key's UTF-8 bytes. Observation MUST NOT rederive keys, change routing, health, retries, settlement ordering, callback ownership, or persistence drain semantics.

#### Scenario: Resolved affinity is recorded without trace
- **WHEN** an HTTP or native WebSocket Responses request resolves affinity and emits a request-log row with tracing disabled
- **THEN** the row stores the source, kind, and hash from that decision
- **AND** the authorized request-log listing returns `stickyKeySource`, `stickyKind`, and `stickyKeyHash`
- **AND** these fields contain no raw keys or prompt content

#### Scenario: Stable grouping across requests
- **WHEN** two requests use the same resolved key
- **THEN** their stored hashes match
- **AND** different synthetic keys with distinct SHA-256 prefixes produce different hashes

#### Scenario: Failure and retry rows retain their meaning
- **WHEN** an existing emitter records an attempt failure or final settlement row
- **THEN** the row records the affinity associated with that attempt or final request state
- **AND** no extra rows are added to simulate an attempt history
- **AND** a final row that covers multiple sends does not claim to enumerate intermediate decisions

#### Scenario: Affinity is absent or unavailable
- **WHEN** resolution explicitly finds no affinity
- **THEN** the source is `none` and kind and hash are null
- **WHEN** the policy has no key after an existing routing adjustment
- **THEN** the hash is null and source retains its original resolved classification
- **AND** kind equals the adjusted policy kind: null when recovery clears it, or `codex_session` when only a broad session key is ignored
- **WHEN** a row predates the migration or no affinity observation was available to its emitter
- **THEN** all three fields are null without an invented backfill

#### Scenario: Existing privacy and retention apply
- **WHEN** request logs are read, retained, or deleted
- **THEN** affinity metadata follows the same access controls and retention lifecycle as its owning row
- **AND** request-log responses without `conversations:read` permission return null for the three affinity fields
- **AND** enabling raw-key tracing does not make these columns store raw keys

#### Scenario: Upgrade and downgrade preserve existing records
- **WHEN** a populated database upgrades to the affinity metadata revision
- **THEN** existing request and ownership records remain intact and historical affinity metadata is null
- **WHEN** that revision is downgraded
- **THEN** only the three new columns are removed and existing record values remain intact

### Requirement: Affinity history converges with dashboard authentication history
The system MUST provide a single migration head when the published affinity history is combined with dashboard role, user authentication and invitation migrations. Convergence MUST preserve all published revision definitions and apply each missing branch once.

#### Scenario: Upgrade either populated history
- **GIVEN** a database at the published affinity merge or the dashboard authentication leaf
- **WHEN** it upgrades to head
- **THEN** existing account ownership, request logs, guest generations, roles, grants, users, identities, invitations and audit history MUST remain intact except for the existing authentication migrations' specified backfills
- **AND** historical logs newly receiving affinity columns MUST retain null affinity metadata

#### Scenario: Merge-only downgrade and reupgrade
- **GIVEN** a database has reached the converged head and a user's credentials or session generation have subsequently changed
- **WHEN** only the merge revision is downgraded and then upgraded again
- **THEN** both parent histories MUST remain applied and all schema and data MUST be preserved
- **AND** the earlier credential projection MUST NOT run again
