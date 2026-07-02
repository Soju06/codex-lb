# claude-oauth-pool Specification (delta)

## ADDED Requirements

### Requirement: Manual Claude account add

The system SHALL expose `POST /api/claude/accounts` to add a Claude account. The body SHALL accept:
- `claudeAccountUuid` (required string)
- `accessToken` (required string)
- `refreshToken` (required string)
- `expiresInSeconds` (required positive integer)
- `scopes` (optional list of strings)
- `userEmail` (optional string)
- `userOrganizationUuid` (optional string)

The system SHALL encrypt `accessToken` and `refreshToken` with the existing `app/core/crypto.py` envelope before persistence, store `claude_access_token_expires_at = now() + expiresInSeconds - skew`, and reject duplicate `claudeAccountUuid` with HTTP 409.

#### Scenario: Add Claude account happy path

- **WHEN** admin submits `POST /api/claude/accounts` with valid `claudeAccountUuid` and tokens
- **THEN** the response returns the new account id
- **AND** tokens are stored encrypted (no plaintext in the row bytes)

#### Scenario: Reject duplicate claudeAccountUuid

- **GIVEN** a Claude account with `claudeAccountUuid='abc-123'` already exists
- **WHEN** admin submits `POST /api/claude/accounts` with the same `claudeAccountUuid`
- **THEN** the system returns 409

#### Scenario: Reject missing required fields

- **WHEN** admin submits `POST /api/claude/accounts` without `refreshToken`
- **THEN** the system returns 400

### Requirement: List Claude accounts

The system SHALL expose `GET /api/claude/accounts` returning a JSON array of accounts with at minimum: `id`, `claudeAccountUuid`, `userEmail`, `userOrganizationUuid`, `isActive`, `claudeAccessTokenExpiresAt`, `lastUsedAt`, `rateLimitRequestsRemaining`, `rateLimitInputTokensRemaining`, `rateLimitOutputTokensRemaining`, `rateLimitStatus`, `createdAt`. Plaintext tokens SHALL NOT be present in the response.

#### Scenario: List does not leak tokens

- **WHEN** admin calls `GET /api/claude/accounts`
- **THEN** the response contains no plaintext access or refresh tokens

#### Scenario: List returns rate-limit cache fields

- **WHEN** admin calls `GET /api/claude/accounts` after some traffic
- **THEN** the rate-limit fields reflect the last persisted state for each account

### Requirement: Disable and re-enable Claude accounts

The system SHALL expose `PATCH /api/claude/accounts/{id}/disable` and `PATCH /api/claude/accounts/{id}/enable`. Disable SHALL set `accounts.status` to `AccountStatus.DEACTIVATED` and record a `deactivation_reason`. Enable SHALL set `accounts.status` back to `AccountStatus.ACTIVE`. While disabled, the account SHALL NOT be selected by the load balancer.

> Note: Account active/inactive state is encoded solely via `accounts.status` (`AccountStatus` enum). The `Account` model has no `is_active` boolean column; all "active vs. inactive" decisions consult the status enum.

#### Scenario: Disabled account is not selected

- **GIVEN** one Claude account has `status=ACTIVE` and one has `status=DEACTIVATED`
- **WHEN** the proxy handles a `/claude/v1/messages` request
- **THEN** only the active account is considered for selection

#### Scenario: Re-enable restores selection

- **WHEN** admin calls `PATCH /api/claude/accounts/{id}/enable` on a previously disabled account
- **THEN** the account's `status=ACTIVE`
- **AND** the account becomes eligible for selection on the next request

### Requirement: Auth guardian refreshes Claude access tokens

The system SHALL run a background pass (extending the existing auth guardian scheduler) that iterates Claude accounts with `claude_access_token_expires_at < now() + 600s` and calls `ClaudeAuthManager.rotate_claude_access_token` for each. Refresh failures with `invalid_grant` SHALL disable the account and emit a structured `claude.refresh.failed` log line. Transient refresh errors SHALL be retried with the same backoff the guardian already uses.

#### Scenario: Expired token is refreshed before the next request

- **GIVEN** a Claude account whose `claude_access_token_expires_at` is in the past
- **WHEN** the auth guardian scheduler pass runs
- **THEN** the account's `claude_access_token_encrypted` and `claude_access_token_expires_at` are updated

#### Scenario: invalid_grant disables the account

- **GIVEN** the auth guardian refreshes a Claude access token
- **AND** Anthropic responds with `invalid_grant`
- **WHEN** the refresh completes
- **THEN** the account's `status=DEACTIVATED`
- **AND** a structured `claude.refresh.failed` log line is emitted

### Requirement: Passthrough /claude/v1/messages

The system SHALL expose `POST /claude/v1/messages` that forwards Anthropic-native request bytes to the Anthropic API and returns the upstream response (streaming or non-streaming) verbatim, with no body translation. The route SHALL require an authenticated API key whose `provider_scope` includes `claude`. The route SHALL select a Claude account from the pool, inject the required auth headers, and write a `request_logs` row with `provider='claude'`.

#### Scenario: Non-streaming request passthrough returns upstream body verbatim

- **GIVEN** a healthy Claude account
- **WHEN** a client calls `POST /claude/v1/messages` with a non-streaming Anthropic request body
- **THEN** the proxy forwards the request to Anthropic
- **AND** the response body matches Anthropic's response bytes

#### Scenario: Streaming request passthrough forwards SSE events

- **GIVEN** a healthy Claude account
- **WHEN** a client calls `POST /claude/v1/messages` with `stream: true`
- **THEN** the proxy streams Anthropic SSE events to the client as they arrive
- **AND** the connection is closed only when Anthropic emits `message_stop`

#### Scenario: Codex-only key is rejected

- **GIVEN** an API key with `providerScope: "codex"`
- **WHEN** a client calls `POST /claude/v1/messages` with that key
- **THEN** the system returns 403

#### Scenario: 503 when no Claude accounts exist

- **GIVEN** no Claude accounts in the pool
- **WHEN** a client calls `POST /claude/v1/messages` with a valid Claude key
- **THEN** the system returns 503 with a JSON error envelope

### Requirement: 401 from Anthropic triggers rotate-and-retry once

When the Anthropic API returns HTTP 401 to a `POST /claude/v1/messages` proxy call, the system SHALL call `ClaudeAuthManager.rotate_claude_access_token(account, force=True)` once and retry the request exactly once. A second consecutive 401 SHALL propagate as a `ClaudeAuthError` to the client.

#### Scenario: 401 then 200 on retry

- **GIVEN** a Claude account with a stale access token
- **WHEN** the proxy request returns 401 from Anthropic
- **THEN** the access token is rotated
- **AND** the request is retried once
- **AND** the second response (typically 200) is returned to the client

#### Scenario: 401 twice propagates as auth error

- **GIVEN** a Claude account whose refresh token is invalid
- **WHEN** the proxy request returns 401 from Anthropic
- **AND** the rotated request also returns 401
- **THEN** the proxy surfaces a 502 to the client
- **AND** the account is marked unhealthy (existing cooldown mechanism)

### Requirement: Anthropic rate-limit headers populate account state

After every Claude request, the system SHALL parse `anthropic-ratelimit-requests-remaining`, `anthropic-ratelimit-requests-reset`, `anthropic-ratelimit-input-tokens-remaining`, `anthropic-ratelimit-input-tokens-reset`, `anthropic-ratelimit-output-tokens-remaining`, `anthropic-ratelimit-output-tokens-reset`, and `anthropic-ratelimit-status` from the upstream response and persist them to the corresponding `accounts.rate_limit_*` columns. When the response is a `429`, the system SHALL also set `accounts.status=RATE_LIMITED` and `accounts.reset_at=<future unix timestamp>`.

#### Scenario: Rate-limit headers update after a 200 response

- **WHEN** Anthropic returns 200 with `anthropic-ratelimit-requests-remaining: 42`
- **THEN** `accounts.rate_limit_requests_remaining` reflects 42 after the request completes

#### Scenario: 429 sets cooldown

- **WHEN** Anthropic returns 429
- **THEN** `accounts.status=RATE_LIMITED` and `accounts.reset_at` is set to a future timestamp
- **AND** `accounts.rate_limit_status` reflects the rejected/limited state

### Requirement: GET /claude/v1/models returns hardcoded catalog

The system SHALL expose `GET /claude/v1/models` returning a JSON object with a `data` array of Claude model descriptors in Anthropic's `models` shape. The model id list SHALL be hardcoded in `app/modules/claude/models_catalog.py` and SHALL include the current set of Max/Pro/Team-eligible Claude model ids at the time of the change. Deprecated model ids SHALL NOT be present in the catalog.

#### Scenario: Models endpoint returns the catalog

- **WHEN** a client calls `GET /claude/v1/models`
- **THEN** the response includes at least one Claude model descriptor
- **AND** no deprecated model ids appear in the response

### Requirement: Soft-delete preserves request history

When a Claude account is disabled, existing `request_logs` rows that reference its `account_id` SHALL remain readable from the dashboard. The `request_logs.account_id` foreign key already uses `ondelete="SET NULL"` in `app/db/models.py`, so historical rows are preserved by default and the soft-disable flow does not need to alter the schema.

#### Scenario: Disabled Claude account keeps request log history

- **GIVEN** a Claude account with `request_logs` history
- **WHEN** the account is disabled via the dashboard
- **THEN** the request logs remain in the database with `account_id` unchanged
- **AND** the dashboard request-log view still shows them, tagged with the disabled account id

### Requirement: Dashboard Claude accounts tab

The SPA dashboard SHALL expose a "Claude Accounts" sidebar entry alongside "Accounts". The tab SHALL show a list of Claude accounts with the same action affordances as the Codex list (disable, enable), an "Add Claude account" button that opens a dialog with the fields from the manual-add requirement, and a usage card per account showing the current rate-limit cache and today's `request_logs.tokens_total`.

#### Scenario: Add Claude account dialog submits to the admin endpoint

- **WHEN** the operator clicks "Add Claude account" and submits valid fields
- **THEN** the dashboard calls `POST /api/claude/accounts`
- **AND** on success, the new account appears in the list

#### Scenario: Empty state when no Claude accounts exist

- **GIVEN** no Claude accounts in the pool
- **WHEN** the operator navigates to the Claude Accounts tab
- **THEN** the tab shows an empty-state message ("Add your first Claude account")
- **AND** the "Add Claude account" button is visible

#### Scenario: Usage card reflects current rate-limit state

- **WHEN** the operator opens a Claude account's usage card
- **THEN** the card displays `rate_limit_requests_remaining`, `rate_limit_input_tokens_remaining`, `rate_limit_output_tokens_remaining`, `rate_limit_status`, and today's tokens total

### Requirement: i18n strings for the Claude tab

The SPA SHALL ship `en` and `zh-CN` translations for the new Claude accounts tab: tab title, add-button label, form labels, error messages, and empty state.

#### Scenario: English locale renders Claude tab strings

- **WHEN** the operator opens the Claude Accounts tab in English locale
- **THEN** all UI text is rendered in English (no fallback key visible)

#### Scenario: zh-CN locale renders Chinese strings

- **WHEN** the operator opens the Claude Accounts tab in zh-CN locale
- **THEN** all UI text is rendered in Simplified Chinese

### Requirement: ProxyService is not modified by this change

The `app/modules/proxy/service.py` ProxyService god object SHALL NOT be modified by this change. The Claude proxy logic SHALL live in `app/modules/claude/service.py` and SHALL communicate with the load balancer and request-log subsystems through the same interfaces the existing ProxyService uses.

#### Scenario: Architecture ratchets remain green

- **WHEN** `make architecture-check` runs against this change
- **THEN** the ProxyService line count, method span, and cross-domain dependency ratchets are unchanged
- **AND** the check exits zero

### Requirement: Verification of Anthropic OAuth contract

Before this change is declared ready, the implementation phase SHALL verify against a real Claude Code CLI token exchange in a sandbox:
- Anthropic OAuth refresh endpoint URL and request/response shape
- Required Anthropic API header set for OAuth-authenticated requests
- Whether Anthropic rotates the refresh token on each access-token refresh
- Exact names and semantics of Anthropic rate-limit response headers

The verification findings SHALL be recorded in `openspec/changes/add-claude-oauth-pool/notes.md` and referenced from the PR description.

#### Scenario: Verification notes are committed

- **WHEN** this change is marked ready for review
- **THEN** `openspec/changes/add-claude-oauth-pool/notes.md` exists
- **AND** it documents each of the four verification bullets above
- **AND** it references the sources used for verification

### Requirement: Anthropic API request headers are pinned

Every `POST /v1/messages` (and other Anthropic Messages API call) sent through the Claude proxy SHALL include exactly the following request headers, in addition to any per-request headers the client already supplies:

- `Authorization: Bearer <access_token>` — the OAuth-issued access token (begins with `sk-ant-oat01-`). `x-api-key` SHALL NOT be sent.
- `Content-Type: application/json` (or `text/event-stream` when streaming).
- `anthropic-version: 2023-06-01` — a date-form version string (NOT semver), stable across the Anthropic Messages API.
- `anthropic-beta: oauth-2025-04-20,claude-code-20250219` — a comma-separated CSV. `oauth-2025-04-20` is REQUIRED for OAuth-authenticated requests; `claude-code-20250219` is strongly recommended (server validates this header on Claude Code's behalf).
- `User-Agent: claude-code/<version>` — recommended (not strictly required); reduces Cloudflare WAF false-positive risk.

The values SHALL match `openspec/changes/add-claude-oauth-pool/notes.md` §2 exactly. If Anthropic ever changes a value, that change MUST be made in `notes.md` first and then propagated here.

#### Scenario: Request to Anthropic includes pinned beta header

- **GIVEN** a healthy Claude account with an OAuth access token
- **WHEN** the proxy sends `POST /v1/messages` to Anthropic
- **THEN** the outbound request includes `anthropic-version: 2023-06-01`
- **AND** it includes `anthropic-beta: oauth-2025-04-20,claude-code-20250219`
- **AND** it includes `Authorization: Bearer <access_token>`
- **AND** it does NOT include `x-api-key`

#### Scenario: Beta header set is the minimum safe set

- **WHEN** the proxy constructs the outbound header set
- **THEN** the only beta flags present in `anthropic-beta` are `oauth-2025-04-20` and `claude-code-20250219`
- **AND** no additional beta flags are appended unless explicitly configured per the future-work policy in `notes.md`

### Requirement: Rate-limit reset values are RFC 3339 only

The rate-limit header parser SHALL parse values of `anthropic-ratelimit-requests-reset`, `anthropic-ratelimit-input-tokens-reset`, and `anthropic-ratelimit-output-tokens-reset` as RFC 3339 timestamps. The parser SHALL NOT accept relative form (e.g. `"in 5m"`) or bare unix seconds. A value that does not parse as RFC 3339 SHALL be dropped (the corresponding `rate_limit_*_reset_at` column remains NULL) rather than coerced.

#### Scenario: RFC 3339 reset value parses to UTC datetime

- **GIVEN** an upstream response with `anthropic-ratelimit-requests-reset: 2026-07-01T12:00:00Z`
- **WHEN** the parser processes the header set
- **THEN** `rate_limit_requests_reset_at` is a `datetime` in UTC corresponding to `2026-07-01T12:00:00Z`

#### Scenario: Malformed reset value is dropped, not guessed

- **GIVEN** an upstream response with `anthropic-ratelimit-requests-reset: in 5m` (relative form)
- **WHEN** the parser processes the header set
- **THEN** `rate_limit_requests_reset_at` is NOT set (column remains NULL)
- **AND** the parser does NOT raise an exception

#### Scenario: Reset value in `Z` suffix form is accepted

- **GIVEN** an upstream response with `anthropic-ratelimit-requests-reset: 2026-07-01T12:00:00Z`
- **WHEN** the parser processes the header set
- **THEN** the value is parsed as RFC 3339 with the `Z` suffix translated to `+00:00` before `datetime.fromisoformat`

### Requirement: Refresh-token rotation is unconditional on every successful refresh

When `ClaudeOAuthClient.refresh` returns a 200 response, the system SHALL overwrite `accounts.claude_refresh_token_encrypted` with the new `refresh_token` returned by Anthropic. The previous refresh token SHALL be considered invalid and SHALL NOT be re-used. The system SHALL NOT have any branch that preserves the existing refresh token when the response includes a new one. If Anthropic ever returns a refresh response without a new `refresh_token` field (which has not been observed in verified captures), the existing refresh token SHALL be discarded (column set to NULL) and the account SHALL be flagged for re-authorization.

#### Scenario: Successful refresh always persists the new refresh token

- **GIVEN** a Claude account with stored refresh token `RT_OLD`
- **WHEN** Anthropic's `POST /v1/oauth/token` returns 200 with `refresh_token: RT_NEW`
- **THEN** `accounts.claude_refresh_token_encrypted` is updated to the encrypted form of `RT_NEW`
- **AND** `RT_OLD` is no longer used by codex-lb

#### Scenario: Stale refresh token yields invalid_grant and disables the account

- **GIVEN** a Claude account whose stored refresh token was already used once
- **WHEN** the proxy attempts to refresh using that stale token
- **AND** Anthropic responds with `400 invalid_grant`
- **THEN** the account's `status=DEACTIVATED`
- **AND** a structured `claude.refresh.failed` log line is emitted
- **AND** no further refresh attempts are made for that account until re-enabled

### Requirement: Per-account refresh serialization (singleflight)

The system SHALL serialize `ClaudeAuthManager.rotate_claude_access_token` calls per `account_id` using a singleflight mechanism (in-process lock keyed on `account_id`, e.g. an `asyncio.Lock` per account or a `dict[account_id, asyncio.Future]` coalescing concurrent callers). If the auth guardian scheduler and a request-time 401-retry path both fire for the same account at the same time, exactly one Anthropic `POST /v1/oauth/token` call SHALL be issued per refresh cycle, and concurrent callers SHALL receive the same rotated credentials. This applies to BOTH the auth guardian refresh path AND the request-time 401-retry path.

#### Scenario: Concurrent guardian + 401-refresh coalesce to one OAuth call

- **GIVEN** a Claude account with a near-expired access token
- **WHEN** the auth guardian scheduler begins a refresh for the account
- **AND** simultaneously, a request-time 401 handler begins a refresh for the same account
- **THEN** the system issues exactly ONE `POST https://platform.claude.com/v1/oauth/token` for that account
- **AND** both the guardian and the request handler receive the same rotated access + refresh tokens
- **AND** Anthropic does not see a second refresh request that could invalidate the first

#### Scenario: Concurrent requests on the same account do not both rotate

- **GIVEN** a Claude account
- **WHEN** two simultaneous `POST /claude/v1/messages` requests both receive 401 from Anthropic
- **THEN** the second request waits on the first request's refresh to complete
- **AND** only one Anthropic OAuth refresh call is issued
- **AND** both requests retry with the same new access token

#### Scenario: Serialization is per-account, not global

- **GIVEN** two distinct Claude accounts `A` and `B` both due for refresh
- **WHEN** the auth guardian scheduler refreshes both at the same tick
- **THEN** the two refresh calls run in parallel (they are not serialized against each other)
- **AND** the singleflight lock for `A` does not block the refresh of `B`