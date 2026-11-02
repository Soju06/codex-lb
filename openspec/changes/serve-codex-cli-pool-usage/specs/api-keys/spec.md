## ADDED Requirements

### Requirement: `/backend-api/wham/usage` serves the pool's Codex rate limits

The system SHALL expose `GET /backend-api/wham/usage` (and the trailing-slash
variant `GET /backend-api/wham/usage/`) returning the Codex
`RateLimitStatusPayload` shape: `plan_type`, `rate_limit` with `allowed`,
`limit_reached`, `primary_window`, `secondary_window` and optional
`monthly_window`, plus optional `credits`, `rate_limit_reset_credits` and
`additional_rate_limits`.

The payload MUST be built from the pool's aggregate usage
(`ProxyService.get_rate_limit_payload`), not from the caller's own ChatGPT
account. `plan_type` MUST be the pool plan type derived from the selected
pool accounts via `_plan_type_for_accounts`. `primary_window` MUST describe
the pool's 5h window, `secondary_window` the pool's 7d window, and
`monthly_window` the pool's monthly window when one exists. Each window MUST
carry `used_percent`, `limit_window_seconds` (300 minutes → `18000`, 10080
minutes → `604800`, 43200 minutes → `2592000`), `reset_after_seconds`, and
`reset_at` (epoch seconds) so the Codex client can map them to
`usedPercent`, `windowDurationMins` and `resetsAt`.

The route MUST require an `Authorization: Bearer` header and MUST NOT be
covered by the router-level proxy API-key guard. Its dedicated dependency
SHALL accept:

- a balancer API key (`sk-clb-...`), which keeps the API-key view used by
  `/api/codex/usage` (own limits, pool aggregate fallback, and
  `hide_upstream_quota_from_api_keys` honoured);
- a ChatGPT OAuth bearer, after exactly one upstream usage validation,
  whether or not its `chatgpt-account-id` belongs to an active pooled
  account. A pooled `chatgpt-account-id` keeps the existing account-scoped
  route resolution; an unknown or absent one uses direct egress.

A missing bearer MUST return 401. An upstream 401/403 MUST return 401, an
upstream 429 MUST return 429, and any other validation failure MUST return
502. The endpoint MUST NOT dispatch a proxied model request or mutate account
health.

#### Scenario: Codex CLI reads the pool through a personal login

- **WHEN** a client calls `GET /backend-api/wham/usage` with a valid ChatGPT
  bearer whose `chatgpt-account-id` is not in the pool
- **AND** one cheap upstream usage validation succeeds
- **THEN** the response is 200 with the pool's windows and the pool's
  `plan_type`
- **AND** the payload contains none of the caller's personal window values

#### Scenario: Missing or invalid bearer

- **WHEN** a client calls `GET /backend-api/wham/usage` without a bearer
- **THEN** the system returns 401 in the OpenAI error format
- **WHEN** the upstream rejects the bearer with 401 or 403
- **THEN** the system returns 401
- **WHEN** the upstream rejects the bearer with 429
- **THEN** the system returns 429

#### Scenario: Balancer API key keeps the API-key view

- **WHEN** a client calls `GET /backend-api/wham/usage` with a valid
  `sk-clb-...` key
- **THEN** the route authenticates it as an API key
- **AND** the payload matches the `/api/codex/usage` API-key payload
- **AND** `hide_upstream_quota_from_api_keys` gates the pool windows exactly
  as it does on `/api/codex/usage`

#### Scenario: Empty pool still answers with a valid plan

- **WHEN** no pool account is selectable
- **THEN** `GET /backend-api/wham/usage` returns 200 with `plan_type` from the
  pool fallback (`guest`) and no windows

### Requirement: `/api/codex/usage` API-key payloads are Codex-readable

The API-key branch of `GET /api/codex/usage` MUST return a `plan_type` that
the Codex client recognises; it MUST NOT return `api_key`. It MUST use the
pool plan type derived via `_plan_type_for_accounts`, or `guest` when the pool
is empty.

When the authenticated key has no credit limits of its own, the branch SHALL
build `rate_limit` from the pool's aggregate upstream credit windows (the
same `_build_aggregate_credit_limits` rows `/v1/usage` exposes as
`upstream_limits`), rather than returning `rate_limit: null`. The fallback
MUST require the key's `usage_sections` to contain `upstream_limits` and MUST
NOT run while `hide_upstream_quota_from_api_keys` is enabled.

Window conversion MUST map `5h`/`daily` to `limit_window_seconds = 18000` or
`86400`, `7d`/`weekly` to `604800`, and `monthly` to `2592000`, with
`used_percent` derived from `current_value / max_value` and `reset_at`
converted from the aggregate row's ISO timestamp to epoch seconds.

#### Scenario: Key without limits gets pool windows

- **WHEN** a valid API key with no credit limits calls
  `GET /api/codex/usage`
- **AND** the pool reports 5h and 7d aggregate credit windows
- **THEN** `rate_limit.primary_window` and `rate_limit.secondary_window` are
  populated from those windows
- **AND** `plan_type` is the pool plan type and not `api_key`

#### Scenario: Key with limits keeps its own windows

- **WHEN** a valid API key with 5h and 7d credit limits calls
  `GET /api/codex/usage`
- **THEN** the windows come from the key's own limits
- **AND** `plan_type` is still the pool plan type

#### Scenario: Privacy toggle suppresses the pool fallback

- **GIVEN** `hide_upstream_quota_from_api_keys` is `true`
- **WHEN** a valid API key with no credit limits calls
  `GET /api/codex/usage`
- **THEN** the response does not expose the pool's aggregate windows

## MODIFIED Requirements

### Requirement: API Key Bearer authentication guard
The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

`/backend-api/wham/usage` SHALL NOT be covered by the router-level API key auth guard scope either; it SHALL authenticate through its dedicated usage-identity dependency (balancer API key or validated ChatGPT bearer).

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: Codex usage excluded from API key guard scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key guard validation is not applied

#### Scenario: Wham usage excluded from API key guard scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/backend-api/wham/usage`
- **THEN** the router-level API key guard is not applied
- **AND** the dedicated usage-identity dependency authenticates the request

### Requirement: Unrecognised `/backend-api/*` reads are relayed upstream

Pointing `chatgpt_base_url` at the balancer moves every account read the Codex
CLI performs, not just the usage one. The system SHALL relay a `GET` for any
`/backend-api/*` path that has no route of its own to `upstream_base_url`,
preserving the caller's query string and credential, and SHALL return the
upstream status, body and content type unchanged.

The relay SHALL NOT attach pool credentials, SHALL require an `Authorization`
bearer, and SHALL be registered after every specific router so
`/backend-api/codex/*` and `/backend-api/wham/usage` keep their own handlers.
Without it, `account/read` fails with "workspace routing discovery failed",
which fails the whole provider probe in clients such as T3 Code.

#### Scenario: Workspace routing discovery stays available

- **WHEN** the CLI requests `GET /backend-api/wham/accounts/check` while
  `chatgpt_base_url` names this balancer
- **THEN** the request is relayed upstream with the caller's bearer and
  `chatgpt-account-id`
- **AND** the caller receives the upstream payload, not a 404

#### Scenario: Pool-owned paths are not relayed

- **WHEN** a request targets `/backend-api/wham/usage` or any
  `/backend-api/codex/*` path
- **THEN** the dedicated route handles it and the relay is not reached
