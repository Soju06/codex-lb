# claude-oauth-pool Specification (delta)

## MODIFIED Requirements

### Requirement: Per-account refresh serialization (singleflight)

The system MUST serialize `ClaudeAuthManager.rotate_claude_access_token` calls per `account_id` so that exactly one `POST /v1/oauth/token` request is issued per account per refresh cycle, even when multiple replicas or scheduler ticks observe a stale access token simultaneously. The system MUST coalesce in-process callers (auth guardian scheduler + request-time 401-retry) onto a single in-flight `asyncio.Task` keyed on `account_id`. In addition, on deployments where more than one process can call `rotate_claude_access_token` for the same `account_id` concurrently, the system MUST serialize those callers with a database-level advisory lock scoped to the account. The cross-process lock MUST use the project's `pg_advisory_xact_lock(hashtext(:key))` idiom with the scope string `"claude-refresh:{account_id}"`. Concurrent callers on a non-leader replica SHALL block on the lock holder and receive the same rotated credentials. On a single-process (SQLite) deployment the in-process singleflight alone is sufficient. Concurrent callers on different `account_id` values MUST NOT block each other.

#### Scenario: Concurrent guardian + 401-refresh coalesce to one OAuth call

- **GIVEN** a Claude account with a near-expired access token
- **WHEN** the auth guardian scheduler begins a refresh for the account
- **AND** simultaneously, a request-time 401 handler begins a refresh for the same account
- **THEN** the system issues exactly ONE `POST https://platform.claude.com/v1/oauth/token` for that account
- **AND** both the guardian and the request handler receive the same rotated access + refresh tokens
- **AND** Anthropic does not see a second refresh request that could invalidate the first

#### Scenario: Two replicas refreshing the same account serialize on the advisory lock

- **GIVEN** a Postgres-backed deployment with two replicas `R1` and `R2`
- **AND** a Claude account `A` whose `claude_access_token_expires_at` is within the refresh skew window
- **WHEN** both replicas call `rotate_claude_access_token(A)` concurrently
- **THEN** exactly one replica issues the `POST /v1/oauth/token` call
- **AND** the other replica waits on the database advisory lock
- **AND** both replicas return the same rotated access + refresh tokens

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

## ADDED Requirements

### Requirement: Refresh-token-less response handling

When `ClaudeOAuthClient.refresh` returns a 200 response that omits the `refresh_token` field, the system MUST:

- emit a structured log line with `event=claude.refresh.refresh_token_missing`, `account_id=<id>`, `severity=warning`, and the original message body excerpt; AND
- set `accounts.status` to `DEACTIVATED` and `accounts.deactivation_reason` to `"refresh_token_missing:<message>"` so the operator is forced to re-authorize.

The system MUST NOT silently coerce a `None` refresh token to the previously stored value, because Anthropic documents single-use refresh-token rotation and a reuse attempt will trigger `invalid_grant`. `rotate_claude_access_token` MUST return `None` to the caller in this branch (matching the `invalid_grant` contract) so the proxy service aborts without retrying.

#### Scenario: Anthropic returns 200 with no refresh_token

- **GIVEN** a Claude account `A` with stored refresh token `RT_OLD`
- **WHEN** `ClaudeOAuthClient.refresh` returns 200 with `{"access_token": "AT_NEW", "expires_in": 3600}` (no `refresh_token` field)
- **THEN** the system emits `event=claude.refresh.refresh_token_missing` at WARNING
- **AND** `accounts.status=DEACTIVATED`
- **AND** `accounts.deactivation_reason="refresh_token_missing:..."`
- **AND** `rotate_claude_access_token` returns `None`

### Requirement: Streaming proxy cleanup on unexpected exceptions

When the streaming proxy path encounters any exception other than the expected typed errors (`ClaudeAuthError`, `ClaudeRateLimited`, `ClaudeUpstreamError`), the system MUST still release the upstream aiohttp response and any connection-pool resources before propagating the exception to the FastAPI `StreamingResponse` layer. The `chunk_kind="sse"` stream emitted by `ClaudeProxyService.stream_messages` MUST terminate cleanly with a single `event: error\ndata: {"error":"<code>"}\n\n` envelope for the three known error classes, and MUST release the iterator's underlying transport in a `finally` block for all exception types.

#### Scenario: Transport disconnect mid-stream releases the iterator

- **GIVEN** an in-progress `POST /claude/v1/messages` streaming request
- **WHEN** the upstream aiohttp response raises `aiohttp.ClientConnectionError` mid-stream
- **THEN** the proxy service's underlying `StreamChunk` iterator's `aclose()` is invoked before the exception propagates
- **AND** the connection is returned to the aiohttp connection pool
- **AND** the FastAPI request handler receives the propagated exception

#### Scenario: Typed error envelope still emitted for known error classes

- **GIVEN** an in-progress `POST /claude/v1/messages` streaming request
- **WHEN** `ClaudeAuthError` is raised by the chat client
- **THEN** the proxy service yields a single `event: error\ndata: {"error":"claude_upstream_auth_error"}\n\n` SSE envelope
- **AND** the underlying `StreamChunk` iterator is closed before the generator returns
