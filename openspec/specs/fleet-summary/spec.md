# fleet-summary Specification

## Purpose
TBD - created by archiving change add-fleet-observability-endpoint. Update Purpose after archive.
## Requirements
### Requirement: Fleet observability requires API key authentication

The system SHALL expose `GET /api/fleet/observability` for trusted local fleet
consumers. The route MUST require a valid Bearer API key even when global proxy
API-key authentication is disabled.

#### Scenario: Missing fleet observability key is rejected

- **WHEN** a client calls `GET /api/fleet/observability` without a Bearer token
- **THEN** the system returns 401
- **AND** no observability payload is returned

### Requirement: Fleet observability reports pressure windows

The system SHALL return read-only Codex pressure windows for the last 30 minutes
and last 2 hours. Each window SHALL include total request count, error count,
input tokens, cached input tokens, output tokens, cost, account breakdown,
request-kind breakdown, and client-group breakdown.

#### Scenario: Valid key returns pressure windows

- **WHEN** a client calls `GET /api/fleet/observability` with a valid Bearer API key
- **THEN** the response includes `pressure.windows[]` entries for `30m` and `2h`
- **AND** warmup traffic and soft-deleted request logs are excluded
- **AND** account-scoped keys only include logs for assigned accounts

### Requirement: Fleet observability reports sticky-session continuity

The system SHALL return read-only sticky-session distribution by account and
kind. Prompt-cache pins older than the configured cache affinity TTL SHALL count
as stale; other sticky-session kinds SHALL not count as stale.

#### Scenario: Valid key returns sticky distribution

- **WHEN** sticky sessions exist for accounts visible to the key
- **THEN** the response includes `sticky.total`, `sticky.recentCount`,
  `sticky.staleCount`, and `sticky.byAccount[]`
- **AND** account-scoped keys only include sticky sessions for assigned accounts

### Requirement: Fleet observability excludes sensitive data

Fleet observability responses MUST NOT include prompt contents, raw request IDs,
archive request IDs, session IDs, sticky-session keys, client IP addresses, API
key identifiers, request error messages, auth tokens, or raw credential data.

#### Scenario: Sensitive fields are omitted

- **WHEN** a valid client calls `GET /api/fleet/observability`
- **THEN** no response object includes raw request identifiers, session
  identifiers, sticky-session keys, client IP addresses, API key identifiers,
  prompt contents, token fields, or raw error payloads

### Requirement: Fleet observability follows fleet usage visibility policy

The endpoint SHALL reuse the fleet summary account scoping and usage visibility
policy. If the authenticated key cannot view account-pool usage, the endpoint
SHALL return a successful non-sensitive payload with no pressure windows and no
sticky-session account distribution.

#### Scenario: Usage visibility disabled

- **WHEN** a valid API key does not include `account_pool_usage`
- **OR** the global API-key quota privacy setting hides upstream quota data
- **THEN** `GET /api/fleet/observability` returns 200
- **AND** the response does not expose request pressure or sticky-session
  account distribution

### Requirement: Fleet summary requires API key authentication

The system SHALL expose `GET /api/fleet/summary` for trusted local fleet
consumers. The route MUST require a valid Bearer API key even when global proxy
API-key authentication is disabled. For callers allowed to view upstream
usage, each account SHALL expose `lastRefreshAt` as OAuth token freshness and
`usageRefreshedAt` as quota-snapshot freshness. `usageRefreshedAt` MUST equal
the newest `recorded_at` value among the persisted usage samples used to build
that account summary, or `null` when no such sample exists.

#### Scenario: Missing fleet summary key is rejected

- **WHEN** a client calls `GET /api/fleet/summary` without a Bearer token
- **THEN** the system returns 401
- **AND** no account summary payload is returned

#### Scenario: Valid fleet summary key returns account capacity

- **WHEN** a client calls `GET /api/fleet/summary` with a valid Bearer API key
- **THEN** the response includes `accounts[]`
- **AND** each account includes `accountId`, `displayName`, `email`, `status`,
  `planType`, `primary`, `secondary`, `lastRefreshAt`, and `usageRefreshedAt`
- **AND** each window includes `remainingPercent`, `resetAt`, and `windowMinutes`

#### Scenario: Usage refresh advances independently of OAuth refresh

- **GIVEN** an account has an existing quota snapshot and OAuth refresh time
- **WHEN** force probe or fleet refresh persists a newer usage sample without
  refreshing OAuth credentials
- **THEN** `usageRefreshedAt` advances to the newer usage sample time
- **AND** `lastRefreshAt` remains unchanged

#### Scenario: Usage freshness is unavailable

- **WHEN** an account has no persisted usage sample
- **THEN** `usageRefreshedAt` is `null`

#### Scenario: Usage visibility is denied

- **WHEN** the authenticated key cannot view upstream usage
- **THEN** `usageRefreshedAt` is `null`
- **AND** `lastRefreshAt` is `null`

### Requirement: Fleet summary excludes sensitive data

Fleet summary responses MUST NOT include OAuth token material, auth token status, raw credit balances, request-cost detail, additional quota detail, or deactivation reasons.

#### Scenario: Sensitive fields are omitted

- **WHEN** a valid client calls `GET /api/fleet/summary`
- **THEN** no response object includes token fields, `auth`, credit-balance fields, request usage, additional quotas, or deactivation reasons

### Requirement: Fleet refresh requests existing usage refresh policy

The system SHALL expose `POST /api/fleet/refresh` for trusted local fleet consumers. The route MUST require a valid Bearer API key even when global proxy API-key authentication is disabled. The route MUST request a usage refresh through codex-lb's existing usage refresh machinery and MUST NOT refresh inside proxy account selection.

The route MUST preserve existing usage-refresh rules for disabled refresh, fresh samples, auth cooldowns, paused accounts, reauth-required accounts, and deactivated accounts.

#### Scenario: Fleet refresh returns minimal outcome

- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** the response includes `ok: true`, `usageWritten`, `accountCount`, `attemptedCount`, and `generatedAt`
- **AND** the response does not include account credentials or token material

#### Scenario: Fleet refresh skips unsafe account states

- **GIVEN** active and paused accounts exist
- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** active accounts are eligible for the refresh attempt
- **AND** paused, reauth-required, and deactivated accounts are not attempted

### Requirement: Fleet refreshes participate in graceful shutdown

The system MUST strongly own every accepted `POST /api/fleet/refresh` task from creation until its dedicated session has finished and closed, regardless of whether its caller remains attached. Task creation and registry insertion MUST occur synchronously before the route first awaits the task. Graceful shutdown MUST wait for all such tracked refreshes for up to `shutdown_drain_timeout_seconds` before stopping usage-refresh singleflight work or closing shared HTTP and database resources. If the deadline expires, the system MUST report each fleet refresh that did not drain before continuing shutdown.

#### Scenario: Caller cancellation does not orphan fleet refresh work

- **GIVEN** a fleet refresh is running in its dedicated session
- **WHEN** the requesting client disconnects or its request task is cancelled
- **THEN** the refresh continues independently of the cancelled caller
- **AND** it remains tracked until its session exits

#### Scenario: Shutdown begins before caller cancellation

- **GIVEN** a fleet refresh was accepted and its caller remains attached
- **WHEN** the in-flight drain times out and graceful shutdown starts draining fleet tasks
- **THEN** the refresh is already present in the fleet task registry
- **AND** cancelling the caller afterward does not remove the refresh from shutdown ownership

#### Scenario: Shutdown waits for a detached fleet refresh

- **GIVEN** a cancelled-request fleet refresh is still pending when graceful shutdown begins
- **WHEN** the refresh completes within the configured drain timeout
- **THEN** shutdown waits for the refresh
- **AND** usage singleflight, shared HTTP clients, and database engines remain available until it finishes

#### Scenario: Overdue fleet refresh is reported

- **GIVEN** a detached fleet refresh remains pending for the full configured drain timeout
- **WHEN** graceful shutdown drains fleet tasks
- **THEN** the drain reports that task as overdue
- **AND** shutdown is allowed to continue

### Requirement: Post-cutoff fleet refreshes are rejected before resource work

Immediately after the in-flight drain attempt returns, graceful shutdown MUST synchronously close fleet task admission before any further shutdown await. A `POST /api/fleet/refresh` request that reaches its producer after this cutoff MUST return the dashboard `503 service_unavailable` error envelope and MUST NOT create a refresh coroutine, task, background session, or other refresh resource work.

#### Scenario: Late fleet producer receives service unavailable

- **GIVEN** graceful shutdown has closed control-plane task admission
- **WHEN** an authenticated caller requests `POST /api/fleet/refresh`
- **THEN** the response status is 503
- **AND** the dashboard error code is `service_unavailable`
- **AND** no fleet refresh task or background session starts

