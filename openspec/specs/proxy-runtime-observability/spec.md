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
The proxy MUST provide an opt-in durable archive of Codex-to-upstream conversation traffic. When enabled, the archive MUST write gzip-compressed newline-delimited JSON records for upstream request payloads, streamed Responses events, compact response payloads, and websocket text or binary frames without performing gzip file I/O in the request event loop during normal operation. The archive writer queue MUST be bounded and MUST apply synchronous write backpressure instead of growing without limit when the background writer is saturated. Archive records MUST include request id, timestamp, direction, traffic kind, transport, account id when known, upstream target metadata, redacted headers, and the full payload or frame body. Credential-bearing headers such as authorization, cookies, proxy authorization, token headers, and API key headers MUST be redacted before persistence. JSON records MUST preserve non-ASCII payload text as UTF-8 rather than Unicode escape sequences. When disabled, no archive file MUST be created by the archive writer.

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

