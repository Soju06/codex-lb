# proxy-runtime-observability Specification

## Purpose

Define proxy observability contracts so runtime failures, routing decisions, and admission rejections remain diagnosable.

## Requirements
### Requirement: Console runtime logs include explicit timestamps
The system SHALL emit server console logs with an explicit timestamp on each line for both application logs and HTTP access logs.

#### Scenario: Server emits an application log
- **WHEN** the runtime writes an application log line to the console
- **THEN** the line includes a timestamp before the log level and message

#### Scenario: Server emits an access log
- **WHEN** the runtime writes an HTTP access log line to the console
- **THEN** the line includes a timestamp before the access-log fields

### Requirement: Optional upstream request summary tracing
When `log_upstream_request_summary` is enabled, the system MUST log one start record and one completion record for each outbound upstream proxy request. For provider-aware routing, each record MUST include the proxy `request_id`, requested route class, selected provider kind, selected routing-subject identifier when available, and enough metadata to correlate the request with the result.

#### Scenario: Responses request tracing is enabled
- **WHEN** the proxy sends an upstream Responses request while `log_upstream_request_summary=true`
- **THEN** the console shows a start record with request metadata and a completion record with status or failure outcome

#### Scenario: Transcription request tracing is enabled
- **WHEN** the proxy sends an upstream transcription request while `log_upstream_request_summary=true`
- **THEN** the console shows the outbound request metadata without logging raw binary body contents

#### Scenario: Provider-aware upstream request tracing is enabled
- **WHEN** the proxy sends an upstream request while `log_upstream_request_summary=true`
- **THEN** the console shows start and completion records that include provider kind, route class, routing-subject identifier or label, and upstream request id when the provider returns one

### Requirement: Optional upstream payload tracing
When `log_upstream_request_payload` is enabled, the system MUST log the normalized outbound payload for JSON upstream requests and MUST log a metadata summary for multipart upstream requests.

#### Scenario: JSON upstream payload tracing is enabled
- **WHEN** the proxy sends an upstream Responses or compact request while `log_upstream_request_payload=true`
- **THEN** the console shows the normalized outbound JSON payload associated with the request id

#### Scenario: Multipart upstream payload tracing is enabled
- **WHEN** the proxy sends an upstream transcription request while `log_upstream_request_payload=true`
- **THEN** the console shows non-binary metadata such as filename, content type, prompt presence, and byte length

### Requirement: Persisted request logs include provider-aware routing fields
Persisted request logs MUST no longer be account-only records. For provider-aware routing, each persisted request log MUST include provider kind, generic routing-subject identifier, requested route class, and upstream request id when available, even when the request fails before upstream selection.

#### Scenario: Persisted request log records a selected provider
- **WHEN** a proxied request selects an upstream routing subject
- **THEN** the persisted request log includes provider kind, routing-subject identifier, route class, and upstream request id when present

#### Scenario: Persisted request log records a pre-routing capability rejection
- **WHEN** the proxy rejects a request before upstream selection because no provider supports the requested route, transport, or continuity capability
- **THEN** the persisted request log still records the requested route class and normalized rejection reason without requiring an `account_id`

#### Scenario: Reservation cleanup failure does not override the proxy result
- **WHEN** request handling has already produced a client response
- **AND** best-effort API-key reservation cleanup fails during post-response teardown
- **THEN** the proxy preserves the original response outcome
- **AND** it logs the cleanup failure without replacing the original response with a cleanup error

### Requirement: Proxy 4xx/5xx responses are logged with provider-aware rejection detail
When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console. When the failure is caused by provider capability gating before routing-subject selection, the log MUST also include the requested route class and rejection reason. For local admission rejections, the log MUST also include which admission lane or stage rejected the request.

#### Scenario: Upstream failure becomes a proxy error response
- **WHEN** an upstream 4xx or 5xx failure is returned to the client by the proxy
- **THEN** the console log includes the proxy response status plus the normalized error code and message

#### Scenario: Local proxy validation or server error is returned
- **WHEN** the proxy itself returns a 4xx or 5xx response before or without an upstream response
- **THEN** the console log includes the local response status plus the error code and message

#### Scenario: Local admission rejection is logged
- **WHEN** the proxy rejects a request locally because a downstream or expensive-work admission lane is full
- **THEN** the console log includes the local response status, normalized error code and message
- **AND** it includes which admission lane or stage rejected the request

#### Scenario: Provider capability mismatch is rejected before selection
- **WHEN** the proxy rejects a request before upstream selection because no provider supports the requested route, transport, or continuity capability
- **THEN** the console log includes the requested route class and normalized rejection code

### Requirement: Provider auth failure transitions are logged with provider context
When provider health changes because of upstream auth failures, the system MUST log the provider kind, routing-subject identifier, and normalized failure reason.

#### Scenario: Platform auth failure changes provider health
- **WHEN** an `openai_platform` identity transitions to unhealthy or deactivated after repeated auth failures
- **THEN** the runtime log includes provider kind, routing-subject identifier, and the normalized provider-auth failure reason
