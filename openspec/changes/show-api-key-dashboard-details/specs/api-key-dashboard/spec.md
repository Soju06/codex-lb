## ADDED Requirements

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

## MODIFIED Requirements

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

### Requirement: API key self-service usage dashboard

After successful key authentication, the SPA SHALL display the authenticated key's privacy-safe profile, configured limits, lifetime requests, total tokens, cached input tokens, and total cost. It SHALL obtain lifetime totals and limit consumption from the existing self-service `/v1/usage` contract and SHALL display recent request logs using the established dashboard grid visual language with only Time, Model, Transport, Status, TTFT, TPS, Tokens, Cost, and Details columns.

The profile presentation MUST distinguish lifecycle metadata from policy values, the usage summary cards MUST use distinct semantic accent colors, and the recent-request column widths MUST be balanced for the privacy-safe column set at desktop widths while preserving horizontal overflow on narrow viewports.

The page SHALL provide refresh, pagination, and disconnect actions. Disconnecting MUST clear the in-memory credential, any remembered credential, and cached key-dashboard data and return to the API key entry screen.

#### Scenario: Render lifetime statistics and recent logs

- **WHEN** a valid API key has profile, usage, configured limits, and request-log history
- **THEN** the page renders its name, masked prefix, active state, lifecycle timestamps, and effective policy values
- **AND** renders limit consumption and reset information
- **AND** renders lifetime request, token, cached-token, and cost totals with distinct accents
- **AND** renders its newest request rows with balanced column widths

#### Scenario: Hide sensitive grid columns and details

- **WHEN** the self-service dashboard renders
- **THEN** raw key, key hash, database ID, account/source assignments, pooled account usage, and internal routing fields are absent
- **AND** Account, Plan, and API Key columns are absent
- **AND** request details do not display administrator-only identity or routing fields

#### Scenario: Refresh and paginate

- **WHEN** the user refreshes or changes the request-log page
- **THEN** the page requests data for the same in-memory API key
- **AND** never exposes the key in the browser URL

#### Scenario: Disconnect from the key dashboard

- **WHEN** the user activates Disconnect
- **THEN** the credential, remembered value, and cached self-service data are cleared from the browser
- **AND** the API key entry screen is shown again
