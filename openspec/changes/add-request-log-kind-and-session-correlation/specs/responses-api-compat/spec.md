## ADDED Requirements

### Requirement: Persist request kind and session correlation in request logs
The service MUST persist a stable `request_kind` value on `request_logs` for proxied request-log rows and MUST expose the same value through `/api/request-logs`. At minimum, standard Responses requests on `/backend-api/codex/responses`, `/v1/responses`, and their websocket equivalents MUST persist `request_kind = "responses"`, compact requests on `/backend-api/codex/responses/compact` and `/v1/responses/compact` MUST persist `request_kind = "compact"`, and transcription requests on `/backend-api/transcribe` or `/v1/audio/transcriptions` MUST persist `request_kind = "transcription"`.

When an inbound proxied request includes a non-empty `session_id` header, the service MUST persist a deterministic hashed `session_id_hash` value on the corresponding request-log row instead of storing the raw `session_id`. The same request-log API response MUST expose that hashed value for correlation. When no `session_id` header is present, the request-log row MAY keep `session_id_hash = null`.

#### Scenario: HTTP compact request log exposes compact kind and hashed session id
- **WHEN** a client completes `/backend-api/codex/responses/compact` with a non-empty `session_id` header
- **THEN** the persisted request log has `request_kind = "compact"`
- **AND** the persisted request log has a non-null `session_id_hash`
- **AND** `/api/request-logs` returns that row with `requestKind = "compact"` and the same `sessionIdHash`

#### Scenario: WebSocket Responses request log exposes responses kind and hashed session id
- **WHEN** a client completes a Responses request over WebSocket on `/backend-api/codex/responses` with a non-empty `session_id` header
- **THEN** the persisted request log has `request_kind = "responses"`
- **AND** the persisted request log has a non-null `session_id_hash`
- **AND** `/api/request-logs` returns that row with `requestKind = "responses"` and the same `sessionIdHash`

#### Scenario: Request without session header leaves session hash empty
- **WHEN** a proxied request completes without any inbound `session_id` header
- **THEN** the persisted request log keeps `session_id_hash = null`
- **AND** `/api/request-logs` returns that row with `sessionIdHash = null`
