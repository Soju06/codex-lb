## MODIFIED Requirements

### Requirement: Full upstream conversation archive
The proxy MUST provide an opt-in durable archive of Codex-to-upstream conversation traffic. When enabled, the archive MUST write gzip-compressed newline-delimited JSON records for upstream request payloads, streamed Responses events, compact response payloads, and websocket text or binary frames without performing gzip file I/O in the request event loop during normal operation. The archive writer queue MUST be bounded and MUST apply synchronous write backpressure instead of growing without limit when the background writer is saturated. Archive records MUST include request id, timestamp, direction, traffic kind, transport, account id when known, upstream target metadata, redacted headers, and the full payload or frame body. Credential-bearing headers such as authorization, cookies, proxy authorization, token headers, and API key headers MUST be redacted before persistence. JSON records MUST preserve non-ASCII payload text as UTF-8 rather than Unicode escape sequences. When disabled, no archive file MUST be created by the archive writer. Admin request-log API rows MUST expose an `archiveRequestId` lookup key when the persisted log id can differ from the archive record request id; guest rows MUST redact that key.

#### Scenario: operator enables archive for audit
- **WHEN** `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED=true`
- **AND** a Codex Responses request is proxied upstream
- **THEN** the archive records both the outbound upstream payload and inbound upstream events or response body as gzip JSONL
- **AND** credential-bearing headers are stored as redacted values

#### Scenario: archive remains disabled by default
- **WHEN** the archive setting is not enabled
- **THEN** the archive writer does not create conversation archive files

#### Scenario: admin views archived traffic
- **GIVEN** conversation archive files exist as `.jsonl.gz` or legacy `.jsonl`
- **WHEN** an authenticated dashboard admin opens an existing request log detail
- **THEN** the dashboard can find matching archive records by request id across archive files and display payload plus metadata for that request

#### Scenario: response-id request logs keep admin archive lookup
- **WHEN** a successful proxied request stores a downstream response id in the request-log `requestId`
- **AND** the conversation archive stored records under the original request context id
- **THEN** the admin request-log API response includes `archiveRequestId` with the original archive lookup id
- **AND** the persisted `requestId` remains available for response-id continuity lookup

### Requirement: Request-log search matches client IP

Request-log search MUST match persisted `client_ip` values for an admin principal. For a guest principal, request-log search MUST NOT inspect or match persisted `client_ip` values.

#### Scenario: Admin searches by client IP

- **GIVEN** a request log row has `client_ip = "203.0.113.7"`
- **WHEN** an admin principal searches request logs for `203.0.113.7`
- **THEN** the matching request log row is returned

#### Scenario: Guest cannot search by redacted client IP

- **GIVEN** a request log row has `client_ip = "203.0.113.7"` and no non-sensitive field matching `203.0.113`
- **WHEN** a guest principal searches request logs for `203.0.113`
- **THEN** the request log row is not returned

## ADDED Requirements

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
