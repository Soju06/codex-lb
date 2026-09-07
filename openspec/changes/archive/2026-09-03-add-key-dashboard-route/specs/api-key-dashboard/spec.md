## Purpose

Provide API key holders with a privacy-safe self-service dashboard for their own lifetime usage and recent requests without granting access to the password-protected operator dashboard.

## ADDED Requirements

### Requirement: Standalone API key dashboard authentication

The SPA SHALL expose `/key-dashboard` outside the dashboard password/session authentication gate. The route MUST present a masked API key input before loading data and MUST authenticate its data requests with the entered API key using the Bearer scheme.

The client MUST NOT place the raw API key in a URL, query-cache key, log message, or durable browser storage. Key-dashboard data requests MUST omit dashboard session cookies and MUST NOT invoke the dashboard-session unauthorized handler when API key authentication fails.

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

#### Scenario: Reject an invalid API key independently

- **WHEN** a user submits a missing, unknown, inactive, or expired API key
- **THEN** the key-dashboard API returns 401
- **AND** the API key entry screen shows an authentication error
- **AND** no dashboard password login flow is triggered

#### Scenario: Global proxy auth is disabled

- **GIVEN** `api_key_auth_enabled` is false
- **WHEN** a user requests key-dashboard data
- **THEN** a valid Bearer API key is still required

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

After successful key authentication, the SPA SHALL display the authenticated key's lifetime requests, total tokens, cached input tokens, and total cost from the existing self-service `/v1/usage` contract. It SHALL display recent request logs using the established dashboard grid visual language with only Time, Model, Transport, Status, TTFT, TPS, Tokens, Cost, and Details columns.

The page SHALL provide refresh, pagination, and disconnect actions. Disconnecting MUST clear the in-memory credential and cached key-dashboard data and return to the API key entry screen.

#### Scenario: Render lifetime statistics and recent logs

- **WHEN** a valid API key has usage and request-log history
- **THEN** the page renders its lifetime request, token, cached-token, and cost totals
- **AND** renders its newest request rows

#### Scenario: Hide sensitive grid columns and details

- **WHEN** the recent request grid renders
- **THEN** Account, Plan, and API Key columns are absent
- **AND** request details do not display administrator-only identity or routing fields

#### Scenario: Refresh and paginate

- **WHEN** the user refreshes or changes the request-log page
- **THEN** the page requests data for the same in-memory API key
- **AND** never exposes the key in the browser URL

#### Scenario: Disconnect from the key dashboard

- **WHEN** the user activates Disconnect
- **THEN** the credential and cached self-service data are cleared from the tab
- **AND** the API key entry screen is shown again
