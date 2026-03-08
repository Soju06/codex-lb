# proxy-runtime-observability Specification

## Purpose
Define the runtime console logging contract for operator-visible proxy observability.

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
When `log_upstream_request_summary` is enabled, the system MUST log one start record and one completion record for each outbound upstream proxy request. Each record MUST include the proxy `request_id`, request kind, upstream target, and enough metadata to correlate the request with the selected account and result.

#### Scenario: Responses request tracing is enabled
- **WHEN** the proxy sends an upstream Responses request while `log_upstream_request_summary=true`
- **THEN** the console shows a start record with request metadata and a completion record with status or failure outcome

#### Scenario: Transcription request tracing is enabled
- **WHEN** the proxy sends an upstream transcription request while `log_upstream_request_summary=true`
- **THEN** the console shows the outbound request metadata without logging raw binary body contents

### Requirement: Optional upstream payload tracing
When `log_upstream_request_payload` is enabled, the system MUST log the normalized outbound payload for JSON upstream requests and MUST log a metadata summary for multipart upstream requests.

#### Scenario: JSON upstream payload tracing is enabled
- **WHEN** the proxy sends an upstream Responses or compact request while `log_upstream_request_payload=true`
- **THEN** the console shows the normalized outbound JSON payload associated with the request id

#### Scenario: Multipart upstream payload tracing is enabled
- **WHEN** the proxy sends an upstream transcription request while `log_upstream_request_payload=true`
- **THEN** the console shows non-binary metadata such as filename, content type, prompt presence, and byte length

### Requirement: Proxy 4xx/5xx responses are logged with error detail
When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console.

#### Scenario: Upstream failure becomes a proxy error response
- **WHEN** an upstream 4xx or 5xx failure is returned to the client by the proxy
- **THEN** the console log includes the proxy response status plus the normalized error code and message

#### Scenario: Local proxy validation or server error is returned
- **WHEN** the proxy itself returns a 4xx or 5xx response before or without an upstream response
- **THEN** the console log includes the local response status plus the error code and message
