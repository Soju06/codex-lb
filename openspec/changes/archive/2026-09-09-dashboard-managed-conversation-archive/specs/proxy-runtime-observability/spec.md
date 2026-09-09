## MODIFIED Requirements

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
