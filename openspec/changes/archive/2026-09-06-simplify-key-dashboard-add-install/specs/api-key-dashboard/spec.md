## MODIFIED Requirements

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

## ADDED Requirements

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
