## MODIFIED Requirements

### Requirement: Authenticated Codex client setup

The Install tab SHALL offer macOS and Linux Bash scripts and a Windows PowerShell script to configure the shared Codex home used by Codex App, CLI, and IDE extension. It MUST offer script copying, installer file download, and a direct curl setup command. The UI MUST explain that setup configures already-installed clients, uses the current key, backs up and replaces existing configuration, authentication, and model catalog, and requires restarting clients. It MUST state that macOS/Linux setup requires Python 3. It MUST explain that WSL or remote extensions require setup in their own environment.

The system SHALL expose `GET /api/key-dashboard/install-script?platform=macos|linux|windows`, require an active unexpired Bearer API key regardless of global proxy authentication settings, and return only that caller's personalized text installer with no-store caching headers. The installer MUST use the dashboard origin's `/backend-api/codex` endpoint and the enforced model or first allowed model; unrestricted keys MUST select the first visible API model in the downloaded catalog. An exported enforced or first allowed model that is absent from the catalog MUST stop setup with an actionable error. Unsupported platforms MUST be rejected. The script MUST NOT disclose account identities or other keys.

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

## ADDED Requirements

### Requirement: Installer refreshes the authorized native model catalog

Every execution of an exported installer MUST fetch `/api/key-dashboard/models` using only the exported codex-lb Bearer key, retain only list-visible API models, preserve their complete native metadata, and write `codex-lb-models.json` in the selected Codex home. The installer MUST configure `model_catalog_json` as the safely encoded absolute file path. It MUST NOT expose upstream credentials, source assignments, or server-side alias mappings.

The catalog endpoint MUST require an active unexpired Bearer API key regardless of global proxy authentication settings, reuse native catalog serialization and key/source scoping, and return private no-store responses without consuming inference limits.

The provider MUST enable WebSockets only when all installed models advertise `prefer_websockets=true`; otherwise it MUST use HTTP Responses. The installer MUST back up and protect the catalog alongside configuration and authentication. Failed downloads, redirects, invalid or empty catalogs, unavailable selected models, unsafe target paths, and backup failures MUST stop setup before replacement and MUST NOT print the credential or response body.

#### Scenario: Install custom aliases with agent metadata

- **GIVEN** a key can access a streaming Responses source with public alias `cd/gpt-6-astra`
- **WHEN** its installer runs successfully
- **THEN** the local catalog contains the public alias and its native instructions, tool capabilities, and agent metadata
- **AND** inaccessible, disabled, non-streaming, and hidden models are not installed
- **AND** the provider uses HTTP and credentials contain only the codex-lb key

#### Scenario: Refresh the catalog on repeat runs

- **GIVEN** an exported script and an existing local catalog
- **WHEN** the operator adds an allowed alias and the user reruns the same script
- **THEN** the newly fetched catalog replaces the previous one and includes the alias
- **AND** the previous catalog remains in the private backup directory

#### Scenario: Preserve WebSockets for a native-only catalog

- **WHEN** every installed model advertises WebSocket preference
- **THEN** the provider retains WebSocket support

#### Scenario: Failed refresh preserves the client setup

- **WHEN** catalog download or validation fails, or a catalog target is a symlink or non-file
- **THEN** existing configuration, authentication and catalog files remain unchanged
- **AND** the script exits with an actionable error without printing credentials

#### Scenario: Optional proxy authentication does not bypass installer scope

- **GIVEN** global proxy authentication is disabled
- **WHEN** an installer downloads its catalog
- **THEN** the endpoint still validates the supplied key and scopes the catalog to its model/source policy
- **AND** missing, invalid, expired or inactive keys are rejected with 401
