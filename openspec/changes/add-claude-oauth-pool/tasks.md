# Tasks

## 0. Verification prerequisites

- [ ] 0.1 Verify Anthropic Claude-Code OAuth refresh endpoint URL and request/response shape against a real Claude Code CLI token exchange (in a sandbox; do not commit real tokens)
- [ ] 0.2 Verify the required Anthropic API header set for OAuth-authenticated requests (`Authorization`, `anthropic-version`, `anthropic-beta`, etc.)
- [ ] 0.3 Verify whether Anthropic rotates the refresh token on each access-token refresh
- [ ] 0.4 Verify the exact names and semantics of Anthropic rate-limit response headers (`anthropic-ratelimit-requests-remaining`, `anthropic-ratelimit-requests-reset`, `anthropic-ratelimit-input-tokens-remaining`, `anthropic-ratelimit-input-tokens-reset`, `anthropic-ratelimit-output-tokens-remaining`, `anthropic-ratelimit-output-tokens-reset`, `anthropic-ratelimit-status`)
- [ ] 0.5 Document the verification findings in `openspec/changes/add-claude-oauth-pool/notes.md` so the implementation phase references a single source

## 1. Database Migration

- [ ] 1.1 Create Alembic revision `add_claude_account_columns` extending `accounts` and `api_keys` tables per design.md §Data model
- [ ] 1.2 Backfill `accounts.provider='codex'` and `api_keys.provider_scope='codex'` for existing rows before `SET NOT NULL`
- [ ] 1.3 Add partial unique index on `(provider, claude_account_uuid)` where `provider='claude'`
- [ ] 1.4 Add CHECK constraints: `accounts.provider IN ('codex','claude')`; `claude_refresh_token_encrypted IS NOT NULL WHEN provider='claude'`
- [ ] 1.5 Provide both `upgrade()` and `downgrade()` paths; ensure single-head upgrade path is maintained
- [ ] 1.6 Run `make migration-check` (sqlite) and `make migration-check-postgres` and confirm both succeed

## 2. Backend Model

- [ ] 2.1 Extend `Account` model in `app/db/models.py` with the columns in design.md §Data model; add `CheckConstraint` and `__table_args__` indexes
- [ ] 2.2 Extend `ApiKey` model with `provider_scope: Mapped[str]` (default `'codex'`)
- [ ] 2.3 Extend `RequestLog` model with nullable `provider: Mapped[str | None]`

## 3. Backend Settings

- [ ] 3.1 Add Anthropic settings to `app/core/config/settings.py`: `claude_api_base_url`, `claude_oauth_token_endpoint`, `claude_oauth_authorize_endpoint` (informational), with sane defaults
- [ ] 3.2 Document the new env vars in `.env.example`

## 4. Backend Anthropic Client

- [ ] 4.1 Implement `ClaudeOAuthClient` in `app/core/clients/anthropic/oauth.py`: `refresh(refresh_token) -> {access_token, refresh_token?, expires_in}`
- [ ] 4.2 Implement `ClaudeChatClient` in `app/core/clients/anthropic/chat.py`: `stream_messages(...)` (SSE passthrough) and `send_messages(...)` (non-streaming passthrough); both return upstream headers
- [ ] 4.3 Implement error classes in `app/core/clients/anthropic/errors.py`: `ClaudeAPIError`, `ClaudeRateLimited`, `ClaudeAuthError`, `ClaudeUpstreamError`
- [ ] 4.4 Wrap outbound HTTP in aiohttp with proxy env support consistent with existing `app/core/clients/`

## 5. Backend Claude Auth Manager

- [ ] 5.1 Implement `ClaudeAuthManager` in `app/modules/claude/auth_manager.py`: `add_claude_account`, `rotate_claude_access_token`, `disable_claude_account`
- [ ] 5.2 Token encryption reuses `app/core/crypto.py` (no new crypto code)
- [ ] 5.3 On `rotate_claude_access_token` failure (e.g. `invalid_grant`), set `accounts.status=DEACTIVATED` with `deactivation_reason` and emit a structured `claude.refresh.failed` log line
- [ ] 5.4 Pydantic schemas in `app/modules/claude/schemas.py` for `AddClaudeAccountRequest`, `ClaudeAccountResponse`, `DisableClaudeAccountRequest`

## 6. Backend Models Catalog

- [ ] 6.1 Define `app/modules/claude/models_catalog.py` with the hardcoded Claude model id list and a `KNOWN_CLAUDE_MODELS` set for validation
- [ ] 6.2 Implement `GET /claude/v1/models` to return the catalog in Anthropic `models` response shape

## 7. Backend Auth Guardian Refresh

- [ ] 7.1 Extend the auth guardian scheduler pass in `app/core/auth/guardian.py` to refresh Claude accounts with `claude_access_token_expires_at < now() + 600s`
- [ ] 7.2 Reuse existing scheduler error handling and rate-limit backoff

## 8. Backend Proxy Service

- [ ] 8.1 Implement `ClaudeProxyService` in `app/modules/claude/service.py` with `stream_or_complete_messages(request_body, *, api_key, request_id)` method
- [ ] 8.2 Account selection: call existing `app/modules/proxy/load_balancer.py::select_account(provider='claude', ...)` (extended to accept provider filter in this change)
- [ ] 8.3 On `401` from upstream: call `rotate_claude_access_token(account, force=True)` once and retry the request once; second `401` raises `ClaudeAuthError`
- [ ] 8.4 Parse Anthropic rate-limit headers and write to `accounts.rate_limit_*` after each request
- [ ] 8.5 Parse `usage` from non-streaming body or final `message_delta` SSE event and write to `request_logs` once per request
- [ ] 8.6 Forward upstream bytes verbatim to the client (no body translation)

## 9. Backend Load Balancer Extension

- [ ] 9.1 Extend `app/modules/proxy/load_balancer.py::select_account` to accept a `provider` argument and filter candidates accordingly
- [ ] 9.2 Add a narrow `if provider == "claude":` branch for cooldown semantics on `429` (set `status=RATE_LIMITED` and `reset_at=<future>`); no other behavior changes for Codex

## 10. Backend API Layer

- [ ] 10.1 Create `app/modules/claude/api.py` with routes:
  - `POST /claude/v1/messages` (streaming and non-streaming via content negotiation)
  - `GET /claude/v1/models`
  - Admin CRUD: `GET /api/claude/accounts`, `POST /api/claude/accounts`, `PATCH /api/claude/accounts/{id}/disable`, `PATCH /api/claude/accounts/{id}/enable`
- [ ] 10.2 Implement `api_key_validator_with_provider('claude')` dependency that rejects keys whose `provider_scope` does not include `'claude'`; reuse on `/claude/v1/*` routes
- [ ] 10.3 Mount the router in `app/main.py`
- [ ] 10.4 Update firewall middleware if needed so existing firewall rules apply uniformly to `/claude/*` (no new namespace in this change)

## 11. Backend API Keys

- [ ] 11.1 Add `provider_scope: str | None = None` to `ApiKeyCreateRequest` and `ApiKeyUpdateRequest` in `app/modules/api_keys/schemas.py`
- [ ] 11.2 Add `provider_scope: str` to `ApiKeyResponse`
- [ ] 11.3 Validate `provider_scope` is a subset of `{'codex','claude'}` on create/update; reject other values with 400
- [ ] 11.4 Default `provider_scope='codex'` when omitted on create

## 12. Frontend API Client

- [ ] 12.1 Extend the frontend API client (likely `frontend/src/lib/api.ts` or co-located hooks) with: `listClaudeAccounts`, `addClaudeAccount`, `disableClaudeAccount`, `enableClaudeAccount`

## 13. Frontend Components

- [ ] 13.1 `ClaudeAccountList.tsx`: table with email, uuid, status, last_used, created_at, actions (disable, enable)
- [ ] 13.2 `AddClaudeAccountDialog.tsx`: form for access_token, refresh_token, expires_in (seconds), scopes (CSV), email (optional), org_uuid (optional); submit calls `addClaudeAccount`
- [ ] 13.3 `ClaudeAccountUsageCard.tsx`: read-only display of `rate_limit_requests_remaining`, `rate_limit_input_tokens_remaining`, `rate_limit_output_tokens_remaining`, `rate_limit_status`, and today's `request_logs.tokens_total`
- [ ] 13.4 Add the "Claude Accounts" sidebar entry in `frontend/src/components/Sidebar.tsx`

## 14. Frontend i18n

- [ ] 14.1 Add new strings to `frontend/src/locales/en.json`: tab title, button labels, form labels, error messages, empty state
- [ ] 14.2 Add the same strings to `frontend/src/locales/zh-CN.json`

## 15. Frontend Type / Schema

- [ ] 15.1 Add `ClaudeAccountSchema`, `AddClaudeAccountRequestSchema`, `ClaudeAccountUsageSchema` to `frontend/src/lib/schemas.ts` (or equivalent location) with TypeScript types
- [ ] 15.2 Extend `ApiKeySchema`, `ApiKeyCreateRequestSchema`, `ApiKeyUpdateRequestSchema`, `ApiKeyResponseSchema` with `provider_scope`

## 16. Metrics

- [ ] 16.1 Add `codex_lb_claude_requests_total`, `codex_lb_claude_refresh_total`, `codex_lb_claude_accounts_active` to the Prometheus registry, gated by `CODEX_LB_METRICS_ENABLED`

## 17. Tests (Unit)

- [ ] 17.1 `tests/unit/test_claude_oauth_client.py`: refresh happy path; `invalid_grant`; server error; refresh-token rotated vs not rotated
- [ ] 17.2 `tests/unit/test_claude_account_service.py`: add happy path; encryption round-trip; soft-delete (disable + re-enable)
- [ ] 17.3 `tests/unit/test_claude_proxy_service.py`: passthrough bytes identical for request body; auth header injection; provider_scope guard rejects mismatched keys
- [ ] 17.4 `tests/unit/test_models_catalog.py`: hardcoded Claude list is non-empty and contains no deprecated ids
- [ ] 17.5 `tests/unit/test_api_key_provider_scope.py`: validator helper accepts/rejects expected scope combinations

## 18. Tests (Integration)

- [ ] 18.1 `tests/integration/test_claude_passthrough.py`: live mock upstream; POST `/claude/v1/messages` non-streaming; SSE streaming round-trip
- [ ] 18.2 `tests/integration/test_claude_usage_headers.py`: mock upstream emits `anthropic-ratelimit-*`; values land in `accounts.rate_limit_*`
- [ ] 18.3 `tests/integration/test_claude_refresh_on_401.py`: first request returns 401 → rotate → retry → 200; second 401 propagates
- [ ] 18.4 `tests/integration/test_claude_migration.py`: `upgrade head` applies; backfill assigns `provider='codex'` to existing rows; `downgrade` restores prior schema
- [ ] 18.5 `tests/integration/test_api_key_provider_scope.py`: key with `provider_scope='codex'` returns 403 on `/claude/v1/messages`; `provider_scope='claude'` succeeds
- [ ] 18.6 `tests/integration/test_request_log_provider.py`: `request_logs.provider` populated correctly for Claude and Codex requests

## 19. OpenSpec Spec Deltas

- [ ] 19.1 `openspec/changes/add-claude-oauth-pool/specs/account-routing/spec.md` — `ADDED Requirements` for provider discriminator on account selection; `MODIFIED Requirements` for the existing account selection requirements
- [ ] 19.2 `openspec/changes/add-claude-oauth-pool/specs/database-migrations/spec.md` — `ADDED Requirements` for `accounts.provider`, `api_keys.provider_scope`, `request_logs.provider`, backfill, partial unique index, downgrade coverage
- [ ] 19.3 `openspec/changes/add-claude-oauth-pool/specs/api-keys/spec.md` — `MODIFIED Requirements` for `provider_scope` field on create/update/response and the route-authz check
- [ ] 19.4 `openspec/changes/add-claude-oauth-pool/specs/proxy-runtime-observability/spec.md` — `ADDED Requirements` for the `codex_lb_claude_*` metric namespace
- [ ] 19.5 `openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md` — new capability spec covering `/claude/v1/*` routes, manual token paste, refresh-on-401, request_logs, dashboard tab

## 20. Final Verification

- [ ] 20.1 `make lint` — clean
- [ ] 20.2 `make typecheck` — clean
- [ ] 20.3 `make test-unit` — clean
- [ ] 20.4 `make test-integration-core` — clean
- [ ] 20.5 `make test-integration-bridge -vv` — clean (existing bridge suites must still pass; new code lives outside `app/modules/proxy/_service/`)
- [ ] 20.6 `make migration-check` and `make migration-check-postgres` — clean
- [ ] 20.7 `make package` — clean (sdist + wheel + asset verify)
- [ ] 20.8 `openspec validate add-claude-oauth-pool --strict --no-interactive` — clean
- [ ] 20.9 `make architecture-check` — clean (ProxyService line-count and method-span ratchets unchanged)
- [ ] 20.10 Document verification outcomes in `openspec/changes/add-claude-oauth-pool/notes.md`
- [ ] 20.11 PR description explains why one PR (not split) and references the verification findings