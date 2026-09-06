# API Key Dashboard Specification

## Purpose

Provide API key holders with a privacy-safe self-service dashboard for their own lifetime usage and recent requests without granting access to the password-protected operator dashboard.

## Requirements

### Requirement: Standalone API key dashboard authentication

The SPA SHALL expose `/key-dashboard` outside the dashboard password/session authentication gate. The route MUST present a masked API key input before loading data and MUST authenticate its data requests with the entered API key using the Bearer scheme.

The client MUST NOT place the raw API key in a URL, query-cache key, or log message. The client MAY store the raw key in browser-local storage only after the user explicitly enables “remember on this browser”; the option MUST be disabled by default, successful authentication MUST precede persistence, and invalid authentication or Disconnect MUST remove the stored value. Key-dashboard data requests MUST omit dashboard session cookies and MUST NOT invoke the dashboard-session unauthorized handler when API key authentication fails.

#### Scenario: Open without a dashboard session

- **GIVEN** dashboard password authentication is required
- **WHEN** a user opens `/key-dashboard`
- **THEN** the API key entry screen renders without requesting a dashboard auth session
- **AND** no administrator dashboard data API is requested

#### Scenario: Submit a valid API key

- **WHEN** a user submits an active, unexpired API key
- **THEN** key-dashboard requests send that value in the `Authorization: Bearer <key>` header
- **AND** the self-service dashboard renders
- **AND** the raw key is no longer present in the input

#### Scenario: Remember a valid API key

- **WHEN** the user explicitly enables “remember on this browser” and submits a valid API key
- **THEN** the raw key is persisted only after authentication succeeds
- **AND** reopening the route restores the key and loads the self-service dashboard without password authentication

#### Scenario: Reject or forget a stored API key

- **WHEN** a remembered key becomes invalid or the user activates Disconnect
- **THEN** the stored credential and all cached self-service data are removed
- **AND** the API key entry screen is shown

#### Scenario: Reject an invalid API key independently

- **WHEN** a user submits a missing, unknown, inactive, or expired API key
- **THEN** the key-dashboard API returns 401
- **AND** the API key entry screen shows an authentication error
- **AND** no dashboard password login flow is triggered

#### Scenario: Global proxy auth is disabled

- **GIVEN** `api_key_auth_enabled` is false
- **WHEN** a user requests key-dashboard data
- **THEN** a valid Bearer API key is still required

### Requirement: Privacy-safe API key profile

The system SHALL expose `GET /api/key-dashboard/profile` using the same mandatory Bearer API key validation as the other self-service endpoints. The response MUST use a dedicated allowlist schema and MAY contain the key name, masked stored prefix, active state, creation/expiration/last-use timestamps, allowed and enforced model settings, allowed and enforced reasoning settings, enforced service tier, traffic class, and transport policy override.

The response MUST NOT contain the raw key, key hash, key database identifier, account or source assignments, pooled account usage, usage-section configuration, or internal routing identifiers.

#### Scenario: Return details for the authenticated key

- **WHEN** an active, unexpired API key requests its profile
- **THEN** the endpoint returns only metadata and policy belonging to that validated key
- **AND** no input parameter can select another key

#### Scenario: Exclude secrets and assignments

- **WHEN** a key profile is returned
- **THEN** the response contains neither the raw key nor its hash or database identifier
- **AND** the response contains no account assignment, source assignment, pooled usage, or internal routing data

#### Scenario: Reject an invalid profile credential

- **WHEN** a missing, unknown, inactive, or expired API key requests the profile
- **THEN** the endpoint returns 401 using the key-dashboard error format

### Requirement: API key scoped recent request logs

The system SHALL expose `GET /api/key-dashboard/request-logs` with `limit` and `offset` pagination. The endpoint MUST derive the API key identifier exclusively from the validated Bearer credential, MUST return only request logs owned by that key, MUST exclude soft-deleted logs, and MUST order results by request time descending with a deterministic newest-first tie break.

The response MUST be defined by a dedicated allowlist schema and MUST NOT contain account identifiers or email, account plan, API key identifier/name/prefix/hash, client IP or user-agent, conversation/archive identifiers, model-source identifiers, upstream-proxy route/pool/endpoint identifiers, or free-form error/failure details. It MAY contain request time and ID, request kind, model and reasoning effort, service tier, transport, normalized status, error code, token/cost totals and breakdown, and latency metrics.

#### Scenario: Return only the authenticated key's logs

- **GIVEN** request logs exist for two different API keys
- **WHEN** one key calls `GET /api/key-dashboard/request-logs`
- **THEN** every returned row belongs to the authenticated key
- **AND** no input parameter can select the other key

#### Scenario: Redact account and API key information

- **WHEN** an authenticated key requests recent logs
- **THEN** no response object contains an account or API key identity field
- **AND** no response object contains client, conversation, source, proxy-route, or free-form failure identity/detail fields

#### Scenario: Paginate newest logs

- **GIVEN** the authenticated key has more logs than the requested limit
- **WHEN** it requests a page with `limit` and `offset`
- **THEN** the response contains the corresponding newest-first slice
- **AND** returns `total` and `hasMore` pagination metadata scoped to that key

### Requirement: API key self-service usage dashboard

After successful key authentication, the SPA SHALL display the authenticated key's privacy-safe profile, configured limits, lifetime requests, total tokens, cached input tokens, and total cost in the default Overview tab. It SHALL obtain lifetime totals and limit consumption from the existing self-service `/v1/usage` contract and SHALL display recent request logs using the established dashboard grid visual language with only Time, Model, Transport, Status, TTFT, TPS, Tokens, Cost, and Details columns.

The profile presentation MUST distinguish lifecycle metadata from models, MUST display only model values in the policy section, and MUST format non-null lifecycle timestamps in browser-local time as `HH:mm:ss  dd/MM/yyyy`, independent of administrator date preferences. The usage summary cards MUST use distinct semantic accent colors, and the recent-request column widths MUST be balanced for the privacy-safe column set at desktop widths while preserving horizontal overflow on narrow viewports.

The page SHALL provide accessible Overview and Install tabs, refresh, pagination, and disconnect actions. Disconnecting MUST clear the in-memory credential, any remembered credential, and cached key-dashboard data and return to the API key entry screen.

#### Scenario: Render lifetime statistics and recent logs

- **WHEN** a valid API key has profile, usage, configured limits, and request-log history
- **THEN** Overview is selected and renders its name, masked prefix, active state, lifecycle timestamps, and enforced model or allowed models (all models when unrestricted)
- **AND** renders limit consumption and reset information
- **AND** renders lifetime request, token, cached-token, and cost totals with distinct accents
- **AND** renders its newest request rows with balanced column widths
- **AND** the profile does not display reasoning, service-tier, traffic-class, or transport policy fields

#### Scenario: Format lifecycle timestamps consistently

- **WHEN** a lifecycle timestamp represents 8:05:09 on 1 August 2026 in the browser timezone
- **THEN** it displays `08:05:09  01/08/2026` regardless of locale or administrator date format
- **AND** missing expiry and last-use dates retain their Never and Not used yet labels

#### Scenario: Hide sensitive grid columns and details

- **WHEN** the self-service Overview renders
- **THEN** raw key, key hash, database ID, account/source assignments, pooled account usage, and internal routing fields are absent
- **AND** Account, Plan, and API Key columns are absent
- **AND** request details do not display administrator-only identity or routing fields

#### Scenario: Refresh and paginate

- **WHEN** the user refreshes or changes the request-log page
- **THEN** the page requests data for the same in-memory API key
- **AND** never exposes the key in the browser URL

#### Scenario: Disconnect from the key dashboard

- **WHEN** the user activates Disconnect
- **THEN** the credential, remembered value, cached self-service data, and installer state are cleared from the browser
- **AND** the API key entry screen is shown again
- **AND** a subsequent successful login starts in Overview

### Requirement: Authenticated Codex client setup

The Install tab SHALL offer macOS and Linux Bash scripts and a Windows PowerShell script to configure the shared Codex home used by Codex App, CLI, and IDE extension. It MUST offer script copying, installer file download, and a direct curl setup command. The UI MUST explain that setup configures already-installed clients, uses the current key, backs up and replaces existing configuration and authentication, and requires restarting clients. It MUST explain that WSL or remote extensions require setup in their own environment.

The system SHALL expose `GET /api/key-dashboard/install-script?platform=macos|linux|windows`, require an active unexpired Bearer API key regardless of global proxy authentication settings, and return only that caller's personalized text installer with no-store caching headers. The installer MUST use the dashboard origin's `/backend-api/codex` endpoint and the enforced model or first allowed model; unrestricted keys MUST leave model selection to the Codex client's default. Unsupported platforms MUST be rejected. The script MUST NOT disclose account identities or other keys.

Generated scripts MUST configure `config.toml` and file-backed `auth.json` in `CODEX_HOME` when set, otherwise the user's `.codex` directory, and MUST back up existing files before replacing them. They MUST safely encode credential, endpoint, and model values as data rather than executable input, MUST NOT print the credential, and MUST restrict credential-file access to the current user. Failure to back up existing files MUST stop setup before replacement.

#### Scenario: Configure the current key on each supported platform

- **WHEN** an authenticated user selects macOS, Linux, or Windows in Install
- **THEN** copy and download actions export the platform-appropriate installer with the current key and endpoint
- **AND** the direct command fetches the same installer with Bearer authorization, without putting the key in the URL or executing a failed download
- **AND** visible previews mask the key and the UI warns that copied commands and files contain credentials

#### Scenario: Preserve recoverable existing configuration

- **GIVEN** existing Codex configuration and authentication files
- **WHEN** the user runs an installer successfully
- **THEN** the old files remain recoverable in a unique private backup directory
- **AND** the new files select codex-lb and authenticate with the exported key

#### Scenario: Reject invalid installer credentials

- **WHEN** a missing, invalid, inactive, or expired credential requests an installer
- **THEN** the endpoint returns 401 with no script
- **AND** a 401 in the Install tab clears the key-dashboard session without invoking administrator authentication

#### Scenario: Discard stale installer responses

- **WHEN** the platform changes, the user disconnects, or the Install tab unmounts during an installer fetch
- **THEN** a late response MUST NOT restore obsolete script or credential state

### Requirement: Clear and responsive installer presentation

The Install tab SHALL visually distinguish platform selection, the direct terminal command, and file export actions. It SHALL display the selected shell and installer filename and group prerequisite, replacement, restart, remote-environment, and credential-export guidance separately from executable previews. Existing Overview content and installer authentication and export behavior MUST remain unchanged.

The presentation MUST support keyboard-operable platform selection with an accessible group name, visible focus, and a selected-state indicator that does not rely only on color. Commands and controls MUST remain readable and operable in light and dark themes at desktop and mobile widths without page-level horizontal overflow.

#### Scenario: Choose a platform and export

- **WHEN** a user chooses macOS, Linux, or Windows in Install
- **THEN** the selected platform, shell, command, filename, and exported content match that choice
- **AND** the direct copy action is distinguishable from script copying and downloading
- **AND** previews remain masked while exports contain the current key

#### Scenario: Use keyboard navigation

- **WHEN** a user navigates to the platform group by keyboard and changes its selection
- **THEN** the selected radio exposes its checked state and visible focus
- **AND** the associated command and export controls update

#### Scenario: Read setup on a narrow screen

- **WHEN** the Install tab is viewed at a 390-pixel viewport in either theme
- **THEN** its cards stack, all setup actions remain usable, and long commands or expanded previews do not cause page-level horizontal overflow
- **AND** prerequisite and security guidance remains visible without expanding the preview
