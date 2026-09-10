# API Key Dashboard Specification

## Purpose

Provide API key holders with a privacy-safe self-service dashboard for their own lifetime usage and recent requests, plus administrator-authorized group usage, without granting access to the password-protected operator dashboard.

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

### Requirement: Route recovery preserves standalone key authentication

Administrator route loading, error recovery, and unknown-route handling SHALL coexist with the standalone `/key-dashboard` route. Opening or recovering the key dashboard MUST NOT require an administrator session or load administrator-only data. Unknown administrator routes SHALL render the administrator not-found experience within the existing administrator authentication boundary.

#### Scenario: Key route remains accessible with administrator authentication required

- **GIVEN** the administrator dashboard requires a password and no administrator session exists
- **WHEN** the user opens `/key-dashboard`
- **THEN** the key entry screen renders without an administrator session request
- **AND** valid key authentication loads only that key's self-service data

#### Scenario: Unknown administrator route recovers without taking over the key route

- **GIVEN** the user is admitted to the administrator dashboard
- **WHEN** the user opens an unknown administrator path
- **THEN** a not-found view offers a path back to the dashboard
- **AND** subsequently opening `/key-dashboard` renders the standalone key route

### Requirement: Administrator-managed key usage groups

Administrator API-key creation and editing SHALL accept an optional `usageGroup` name of at most 128 characters after trimming surrounding whitespace. Names SHALL be case-sensitive; blank or null SHALL mean ungrouped. Keys with the same non-empty name SHALL belong to one usage-sharing group. Omitting the property during update MUST preserve membership. Existing keys MUST remain ungrouped after migration. Only callers with dashboard write access SHALL assign or remove membership.

#### Scenario: Assign and remove a member

- **WHEN** an administrator assigns the same group name to two keys in the API-key forms
- **THEN** both keys can see the group's aggregate statistics
- **AND** clearing one key's group removes its access and its entry on subsequent group reads

#### Scenario: Preserve existing installations and updates

- **WHEN** an existing database is upgraded or a key is edited without `usageGroup`
- **THEN** upgrade leaves historical keys ungrouped and unrelated edits preserve existing membership
- **AND** regenerating a key preserves membership

### Requirement: Privacy-safe thirty-day group usage

`GET /api/key-dashboard/group` SHALL require an active, unexpired Bearer key regardless of the global proxy authentication setting. The server MUST derive current membership from persisted data using that credential and MUST NOT accept a caller-selected group or key. Ungrouped keys SHALL receive a null group and an empty member list. Grouped keys SHALL receive current members, including the caller and inactive or expired members, with zero totals for unused keys, sorted by display name with a deterministic tie-break. Deleted or reassigned keys MUST NOT appear.

The response SHALL contain the group name, UTC `from` and `until` timestamps, and per-member display name, masked prefix, caller indicator, request count, total tokens, cached input tokens, and USD cost for `[until - 30 days, until)`. It MUST NOT expose raw keys, hashes, database IDs, peer request logs, limits, account/source/routing assignments, or client identity. Statistics SHALL exclude warmup and limit-warmup requests and include preserved historical usage after account deletion. Retained hourly aggregates SHALL contribute without double counting raw history; unavailable partial-hour edges after raw-log retention SHALL follow the existing usage-rollup boundary semantics.

#### Scenario: Isolate group statistics and the time window

- **GIVEN** keys in two groups have recent and older usage
- **WHEN** a member requests group statistics
- **THEN** only its current group's members and usage within the preceding 30 days are returned
- **AND** caller-supplied selectors cannot expand that scope

#### Scenario: Membership removal takes effect on the next read

- **GIVEN** a member's authentication metadata has been cached
- **WHEN** an administrator removes or changes its group
- **THEN** the next group read uses current persisted membership

#### Scenario: Reject invalid credentials

- **WHEN** a missing, unknown, inactive, or expired key requests the group endpoint
- **THEN** it receives the independent key-dashboard 401 response

#### Scenario: Preserve aggregated history

- **GIVEN** hourly aggregates cover older requests and newer requests remain in raw logs
- **WHEN** a group member loads statistics
- **THEN** folded requests count once and newer requests also contribute

### Requirement: Group keys dashboard tab

The authenticated key dashboard SHALL provide an accessible Group keys tab alongside Overview and Install. It SHALL load group data only when opened, display the 30-day period, group totals and each member's request/token/cache/cost totals, identify the caller, and show a clear ungrouped state directing the holder to an administrator. Group loading errors SHALL offer retry. Refresh SHALL reload the active group's data. Disconnect or any group 401 MUST clear the credential and group data; unmounting or replacing a request MUST discard late responses. The member table MUST remain usable on narrow screens without page-level horizontal overflow.

#### Scenario: Inspect and refresh group usage

- **WHEN** a grouped key opens Group keys and activates Refresh
- **THEN** it sees the group totals and each member's statistics over the fixed 30-day period from a refreshed response

#### Scenario: Ungrouped key and failed requests

- **WHEN** an ungrouped key opens Group keys
- **THEN** it sees a no-group message
- **AND** a failed group request displays an error and retry action

#### Scenario: Leave the group tab or disconnect during loading

- **WHEN** a pending group request completes after the tab unmounts or the user disconnects
- **THEN** its response does not restore old group data or credentials

### Requirement: Daily group usage series

The group response SHALL include, for every member, a `dailyUsage` array containing one entry per UTC calendar date intersecting `[from, until)`. Each entry SHALL contain the date, total tokens, and USD cost for that member. The service MUST include zero-valued entries for dates without usage, preserve the rolling window's partial boundary semantics, and calculate member summary token and cost totals from the same daily values. Retained hourly rollups and raw request windows MUST produce equivalent daily values without double counting.

#### Scenario: Render a dense rolling series

- **WHEN** a grouped key requests usage across a rolling thirty-day window
- **THEN** every member receives the same ordered UTC date sequence
- **AND** unused dates contain zero tokens and zero cost
- **AND** the first or last date may contain only the portion inside the rolling window

#### Scenario: Preserve daily values across aggregation boundaries

- **GIVEN** requests span UTC midnight and some older requests have been folded into hourly rollups
- **WHEN** the group usage endpoint reads the window before and after folding
- **THEN** each request contributes to the UTC date of its request timestamp exactly once
- **AND** the member summary totals equal the sums of its daily series

### Requirement: Interactive daily group chart

The Group keys tab SHALL render a responsive, accessible daily chart after group totals. It MUST provide Tokens and Cost (USD) metric controls, one distinguishable line per member, per-member visibility controls, exact-value tooltips, and an accessible tabular view of the currently selected metric. Toggling metrics or members MUST use the loaded response without another network request. The chart and table MUST remain usable at a 390-pixel viewport without page-level horizontal overflow, and unmounting, disconnecting, refreshing, or receiving a group 401 MUST clear the chart with the rest of group state.

#### Scenario: Compare metrics and members

- **WHEN** a user switches from Tokens to Cost or hides a member
- **THEN** axes, tooltip values, table values, and visible lines update to that selection
- **AND** the other members' values remain available without refetching

#### Scenario: Use the accessible daily table

- **WHEN** a user expands the daily data disclosure
- **THEN** a labeled table lists each UTC date and the selected metric for every visible member
- **AND** the table remains keyboard operable and scrollable within the chart card on narrow screens
