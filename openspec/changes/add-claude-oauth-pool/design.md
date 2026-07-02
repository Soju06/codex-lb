## Context

codex-lb is built around ChatGPT/Codex: `auth.openai.com` issues OAuth tokens for the ChatGPT backend, and clients hit `chatgpt.com/backend-api/codex` (Codex-native) or `/v1/*` (OpenAI-compatible). Today every account in the pool is a Codex account.

Users on Claude Max / Pro / Team plans obtain OAuth tokens through claude.ai and authenticate them against the Anthropic API via Claude Code. Those tokens behave like user-owned OAuth credentials with a per-account quota envelope enforced server-side by Anthropic. codex-lb should be able to pool them, just as it pools ChatGPT OAuth tokens today.

## Goals / Non-Goals

**Goals:**
- Pool multiple Claude Max/Pro/Team OAuth tokens; load-balance them like Codex accounts.
- Pass Anthropic-native request and response bytes through unchanged; no OpenAI ↔ Anthropic translation.
- Refresh tokens on schedule (auth guardian scheduler) and on 401 (request-time rotate-and-retry, once).
- Surface Claude accounts in the existing dashboard with a minimal UI surface (list, add, disable, soft-delete, read-only usage card).
- Make provider selection explicit and URL-bound: `/v1/*` is the Codex pool, `/claude/v1/*` is the Claude pool. The API key's `provider_scope` determines which URL namespace the key can hit.
- Reuse existing infrastructure for load balancing, sticky sessions, request logs, rate-limit tracking, firewall, dashboards.

**Non-Goals (this change):**
- OpenAI ↔ Anthropic format translation.
- PKCE OAuth flow inside codex-lb (deferred to a follow-up change; users paste tokens manually here).
- Quota-planner UI for Claude (deferred; rate-limit headers are still recorded so a later change can surface them in the planner).
- Per-day / per-week usage trend charts for Claude accounts (deferred).
- Request-log filter UI by `provider` (deferred).
- Multi-org Claude accounts with separate quotas (single-org-per-account is enough for the first change).
- Tools/function-calling-specific Anthropic surface (passthrough carries tools verbatim; no client-side validation).

## Decisions

### Decision 1: Single `accounts` table with `provider` discriminator (Approach A)

**Rationale**: Reuses the existing load balancer, account-cache, sticky-session pool, request logs, firewall, and dashboard-card components without duplication. Existing Codex code paths get a small `if provider == "claude":` branch where genuinely needed, and stay untouched everywhere else. The architectural-fitness ratchets in `scripts/check_proxy_architecture.py` are not stressed. The change is small enough to fit the ~800-net-lines PR guideline (with documented justification for landing the dashboard tab in the same PR).

**Alternatives considered:**
- **Parallel `claude_accounts` table + parallel subsystems** (Approach B): Cleanest isolation but doubles scaffolding (CRUD, refresh, balancer, sticky, dashboard cards) and pushes the PR well over the ~800-net-lines gate. Rejected because YAGNI for a first change.
- **Provider protocol abstraction with Codex flow rewritten to use it** (Approach C): Most extensible long-term but directly violates ADR-0001 (ProxyService decomposition is in flight) and would touch the god object being ratcheted down. Rejected as unrelated refactoring.

### Decision 2: Manual token paste in this change; PKCE flow in a follow-up change

**Rationale**: Manual token paste (access token, refresh token, expires_in, optional email, optional org UUID) gives users a working pool without codex-lb having to implement OAuth templates, callback routes, PKCE state, or browser-handshake UI. The Claude Code CLI exposes its own OAuth flow; users run it once and paste the result. PKCE in codex-lb is a separate UX feature worth its own change so it can carry its own dashboard work in isolation.

**Alternatives considered:**
- **PKCE flow in this change**: More UX-complete but adds templates, callback handler on port 1455, schema for `state`/PKCE-verifier lifecycle, and OAuth error surfaces — roughly doubles the change's surface.
- **Manual paste only forever**: Fine, but PKCE is a clear UX win; better as a follow-up than a permanent YAGNI decision.

### Decision 3: URL-bound provider namespace (`/v1/*` vs `/claude/v1/*`)

**Rationale**: `/v1/*` keeps the existing OpenAI-compatible surface (Codex-only) untouched. `/claude/v1/*` is a fresh passthrough namespace carrying Anthropic-native bytes. The API key's `provider_scope` is the cross-check: a Codex-only key gets 403 on `/claude/v1/*` and vice versa. The trade-off — clients need to know which URL namespace targets which provider — is acceptable because clients already configure their `baseURL` per provider in codex-lb's `codex-lb/v1` and `codex-lb/backend-api/codex` examples today.

**Alternatives considered:**
- **Single `/v1/*` with model-name routing**: Maximum client transparency, but every `/v1/*` route needs an OpenAI ↔ Anthropic translator (Request body, response body, streaming SSE event names, rate-limit headers, usage extraction). Two orders of magnitude more code and tests. Rejected for the first change.
- **API-key-based pool splitting (no URL separation)**: Each API key has a `claude_enabled` boolean; both providers reach `/v1/*`. Loses the clean URL-bound mental model and conflates provider routing with key authz.
- **Both translator and passthrough**: Doubles surface and review risk for a first change.

### Decision 4: Refresh on 401 (request-time rotate-and-retry, once)

**Rationale**: A 401 from Anthropic means the access token has expired ahead of schedule or was rotated server-side. The cleanest UX is to refresh and retry once. Two consecutive 401s mark the account unhealthy via the existing `AccountStatus` enum (set to `RATE_LIMITED` with `reset_at` far enough in the future that the load balancer excludes it and the auth guardian scheduler can re-attempt refresh).

**Alternatives considered:**
- **Scheduled refresh only**: Simpler code but allows minutes of stale-token failures before the next scheduler tick.
- **Refresh + retry + unhealthy-on-second-401**: Same as chosen option; that is what we mean by "once".

### Decision 5: One shared firewall namespace for `/v1/*` and `/claude/v1/*`

**Rationale**: `api-firewall` already has a per-route namespace concept. Adding a separate `claude` namespace for the first change would let operators split Codex and Claude access by IP, but doubles the firewall test surface and dashboard config for a feature whose access-control story is mostly "API key authorized for provider". One shared namespace preserves the existing rule semantics; if operators later need to split, that's a small follow-up change.

### Decision 6: Soft-delete (with retention) for Claude accounts

**Rationale**: Existing `accounts/service.py::disable_account` already implements soft-delete with `is_active=false` while preserving `request_logs.account_id` history. Reusing it preserves the existing "you can re-enable a disabled account" UX and avoids orphan `account_id` references in `request_logs`.

### Decision 7: Usage recorded once per request, after the final SSE event for streams

**Rationale**: Matches the existing Codex behavior (`ProxyService` writes a single request-log row per request after the stream completes). Anthropic emits usage on the final `message_delta` event before `message_stop`, so a single write at stream end captures both rate-limit headers and usage tokens without per-chunk DB chatter.

## Architecture

### New module layout

```
app/modules/claude/
├── __init__.py
├── api.py                     # FastAPI router for /claude/v1/*
├── service.py                 # ClaudeProxyService (passthrough + retry-on-401)
├── auth_manager.py            # ClaudeAuthManager (account CRUD, token storage)
├── schemas.py                 # Pydantic schemas for add/list/disable Claude accounts
├── models_catalog.py          # hardcoded list of supported Claude model ids
└── _client/                   # thin wrappers over app/core/clients/anthropic
    ├── chat.py                # Anthropic passthrough SSE + response handling
    └── oauth.py               # Anthropic OAuth refresh + token exchange

app/core/clients/anthropic/
├── __init__.py
├── oauth.py                   # ClaudeOAuthClient: refresh + access token exchange
├── chat.py                    # ClaudeChatClient: stream/complete /v1/messages
└── errors.py                  # ClaudeAPIError, ClaudeRateLimited, ClaudeAuthError
```

### Data model

`accounts` table (one Alembic revision, downgrade present, single-head graph):

| Column | Type | Default | Notes |
|---|---|---|---|
| `provider` | `TEXT NOT NULL` | `'codex'` (backfilled in `upgrade()`) | `CHECK (provider IN ('codex','claude'))` |
| `claude_account_uuid` | `TEXT NULL` | — | Required for `provider='claude'`; partial unique index `(provider, claude_account_uuid)` |
| `claude_refresh_token_encrypted` | `BLOB NULL` | — | Encrypted via existing `app/core/crypto.py` |
| `claude_access_token_encrypted` | `BLOB NULL` | — | Encrypted access token |
| `claude_access_token_expires_at` | `DATETIME NULL` | — | Set to `issued + expires_in - skew` |
| `claude_scopes` | `TEXT NULL` | — | JSON array of OAuth scopes, optional |
| `claude_user_email` | `TEXT NULL` | — | Optional display label; nullable |
| `claude_user_organization_uuid` | `TEXT NULL` | — | For Team/Enterprise plan displays |
| `rate_limit_requests_remaining` | `INTEGER NULL` | — | Parsed from `anthropic-ratelimit-requests-remaining` |
| `rate_limit_requests_reset_at` | `DATETIME NULL` | — | Parsed from `anthropic-ratelimit-requests-reset` |
| `rate_limit_input_tokens_remaining` | `INTEGER NULL` | — | From `anthropic-ratelimit-input-tokens-remaining` |
| `rate_limit_input_tokens_reset_at` | `DATETIME NULL` | — | From `anthropic-ratelimit-input-tokens-reset` |
| `rate_limit_output_tokens_remaining` | `INTEGER NULL` | — | From `anthropic-ratelimit-output-tokens-remaining` |
| `rate_limit_output_tokens_reset_at` | `DATETIME NULL` | — | From `anthropic-ratelimit-output-tokens-reset` |
| `rate_limit_status` | `TEXT NULL` | — | `allowed`/`rejected`/`limited` from `anthropic-ratelimit-status` |

`api_keys` table:

| Column | Type | Default | Notes |
|---|---|---|---|
| `provider_scope` | `TEXT NOT NULL` | `'codex'` (backfilled) | CSV of `'codex'`, `'claude'`. The API exposes this as a deduplicated, sorted JSON array of strings (`list[str]`); the field round-trips losslessly between the JSON array form and the CSV column. Validated as a subset of allowed values on create/update. |

`request_logs` table:

| Column | Type | Default | Notes |
|---|---|---|---|
| `provider` | `TEXT NULL` | — | `'codex'` or `'claude'`. Set by the proxy layer. Existing rows remain NULL. |

### Account selection and routing

- Use the existing `app/modules/proxy/load_balancer.py::select_account(provider='claude', ...)` flow. The provider filter restricts the candidate pool to Claude accounts, after which affinity, sticky sessions, and quota cooldowns apply exactly as for Codex accounts.
- A minimal `if provider == "claude":` branch is allowed in the load balancer to give Claude accounts the same rate-limit-driven cooldown behavior Codex accounts already get (Anthropic `429` or `anthropic-ratelimit-status: rejected` → `AccountStatus.RATE_LIMITED` + `accounts.reset_at` set to a future unix timestamp, matching the existing pattern in `app/modules/proxy/load_balancer.py`).

### Auth and refresh

- `ClaudeAuthManager.rotate_claude_access_token(account, *, force=False)`:
  - Decrypt refresh token, call `ClaudeOAuthClient.refresh(refresh_token)`.
  - Re-encrypt and persist new access + refresh (if rotated) + new expiry.
  - On `invalid_grant` or unrecoverable error, set `accounts.status=DEACTIVATED` (with `deactivation_reason`) and emit a structured log line.
- Auth guardian scheduler extended with a single new pass: iterate accounts with `provider='claude'` and `claude_access_token_expires_at < now() + 600s`, calling `rotate_claude_access_token`. The existing scheduler instance, error handling, and rate-limit backoff are reused.
- `ClaudeChatClient.send_messages(...)` and `.stream_messages(...)`:
  - Inject auth headers with exact values verified by Phase 0 (see `notes.md` §2):
    - `Authorization: Bearer <access_token>` — OAuth-issued access tokens begin with `sk-ant-oat01-`. `x-api-key` MUST NOT be sent.
    - `Content-Type: application/json` (or `text/event-stream` when streaming).
    - `anthropic-version: 2023-06-01` — date-form version string (NOT semver). Stable across all Anthropic Messages API calls.
    - `anthropic-beta: oauth-2025-04-20,claude-code-20250219` — comma-separated CSV of beta flags. `oauth-2025-04-20` is REQUIRED for OAuth-authenticated requests. `claude-code-20250219` is strongly recommended for Claude Code fidelity (the server validates it on Claude Code's behalf).
    - `User-Agent: claude-code/<version>` recommended (not strictly required; reduces Cloudflare WAF false-positive risk).
  - On non-streaming response: forward body bytes after recording usage and rate-limit headers.
  - On streaming response: forward SSE bytes to the client; on stream end, parse the final `message_delta` and the response trailers / headers to extract usage and rate-limit state.
  - On `401` from upstream: call `ClaudeAuthManager.rotate_claude_access_token(account, force=True)` once and retry the request once. Second `401` propagates as `ClaudeAuthError`.
  - **Per-account refresh serialization**: Anthropic's OAuth does NOT support multiple active refresh tokens for the same `client_id`. If the auth guardian scheduler and the request-time 401-retry path both fire for the same `account_id` simultaneously, the second refresh invalidates the first and yields `400 invalid_grant`. Both paths MUST coalesce through a singleflight lock keyed on `account_id` (e.g. `asyncio.Lock` per account, an in-process `dict[account_id, asyncio.Future]`, or equivalent). Concurrent callers waiting on the lock receive the same rotated credentials rather than racing.

### API-key authorization

- New helper `api_key_validator_with_provider(provider: str)` returns a FastAPI dependency that resolves and validates the API key and rejects keys whose `provider_scope` does not include `provider`. Used by `app/modules/claude/api.py`.
- API key CRUD learns the optional `provider_scope` field; default on create is `'codex'` to preserve existing behavior.

### Rate-limit handling

- Anthropic rate-limit headers (list above) are written into the `accounts.rate_limit_*` columns after every request.
- Reset values (`anthropic-ratelimit-*-reset`) are absolute **RFC 3339** timestamps (e.g. `2026-07-01T12:00:00Z`). Verified across all captures; no relative form ("in 5m") or unix seconds form has been observed in Anthropic responses. The parser SHALL accept RFC 3339 only and SHALL reject malformed values by dropping the field rather than guessing.
- `429` responses set `accounts.status=AccountStatus.RATE_LIMITED` and `accounts.reset_at=<future unix timestamp derived from the nearest RFC 3339 reset value>` and `accounts.rate_limit_status='rejected'`, matching existing Codex cooldown semantics in `app/modules/proxy/load_balancer.py`.

### Request logs and metrics

- `request_logs` row written once per request after stream completion (or once per non-streaming response). Existing schema absorbs Anthropic `usage` field semantics: `input_tokens → tokens_input`, `output_tokens → tokens_output`, `cache_creation_input_tokens → cached_input_tokens` (existing field, see `app/modules/request_logs`).
- New Prometheus namespace `codex_lb_claude_*` (gated by `CODEX_LB_METRICS_ENABLED`):
  - `codex_lb_claude_requests_total{status}`
  - `codex_lb_claude_refresh_total{result}`
  - `codex_lb_claude_accounts_active` (gauge)

### Frontend (minimal)

```
frontend/src/components/claude/
├── ClaudeAccountList.tsx       # table: email | uuid | status | last_used | created_at | actions
├── AddClaudeAccountDialog.tsx  # form: access_token, refresh_token, expires_in, scopes, email, org_uuid
└── ClaudeAccountUsageCard.tsx  # read-only display of rate_limit_* fields and usage_today
```

i18n strings (en + zh-CN) for the new UI: tab title, button labels, error messages, empty state.

## Files to Add / Modify

### New files

- `openspec/changes/add-claude-oauth-pool/{proposal.md,design.md,tasks.md}`
- `openspec/changes/add-claude-oauth-pool/specs/{account-routing,database-migrations,api-keys,proxy-runtime-observability,claude-oauth-pool}/spec.md`
- `app/modules/claude/{__init__,api,service,auth_manager,schemas,models_catalog}.py`
- `app/modules/claude/_client/{__init__,chat,oauth}.py`
- `app/core/clients/anthropic/{__init__,oauth,chat,errors}.py`
- `app/db/alembic/versions/<rev>_add_claude_account_columns.py`
- `frontend/src/components/claude/{ClaudeAccountList,AddClaudeAccountDialog,ClaudeAccountUsageCard}.tsx`
- `tests/unit/test_claude_{oauth_client,account_service,proxy_service,models_catalog}.py`
- `tests/integration/test_claude_{passthrough,usage_headers,refresh_on_401,migration,api_key_provider_scope}.py`

### Modified files

- `app/db/models.py` — add new columns on `Account`, `ApiKey`, `RequestLog`; CHECK constraints; partial unique indexes.
- `app/modules/accounts/service.py` — extend with `add_claude_account`, `rotate_claude_access_token`, `disable_claude_account`.
- `app/core/auth/guardian.py` — extend scheduler pass to refresh Claude tokens.
- `app/modules/proxy/load_balancer.py` — accept `provider` filter; add provider-aware cooldown branch.
- `app/modules/api_keys/{api,schemas,service}.py` — `provider_scope` field on create/update/response.
- `frontend/src/lib/api.ts` (or equivalent API client) — Claude account CRUD methods.
- `frontend/src/locales/{en,zh-CN}.json` — new i18n strings.
- `frontend/src/components/Sidebar.tsx` — new "Claude Accounts" nav entry.
- `app/main.py` — include `app.modules.claude.api.router`.
- `app/core/config/settings.py` — new optional settings (Anthropic API base URL, OAuth endpoint URLs) with defaults.

## Risks / Trade-offs

- **Verification status (Phase 0 complete)**: Anthropic OAuth refresh endpoint URL (`https://platform.claude.com/v1/oauth/token`), required API header set (`Authorization: Bearer`, `anthropic-version: 2023-06-01`, `anthropic-beta: oauth-2025-04-20,claude-code-20250219`), and refresh-token rotation behavior (always rotate, single-use) have been verified against public sources (Anthropic docs + multiple open-source clients). See `openspec/changes/add-claude-oauth-pool/notes.md` for the full citation list.
- **PR size**: Estimated 1100-1200 net lines (Python ~700, frontend ~300, OpenSpec ~200). Exceeds the ~800-net-lines guidance. Justification: end-to-end functionality requires the database column, account lifecycle, OAuth refresh, passthrough route, API-key authz, and minimal dashboard tab to land together for the feature to be testable. PR description must document this rationale.
- **Refresh-token rotation (verified)**: Anthropic rotates the refresh token on every successful `POST /v1/oauth/token` response. Reusing a stale refresh token yields `400 invalid_grant`. The implementation MUST unconditionally overwrite `claude_refresh_token_encrypted` with the new `refresh_token` from each refresh response. If a future Anthropic change ever stops rotating, the unconditional overwrite remains harmless (the new value equals the old one). The implementation MUST NOT preserve the previous refresh token under any branch.
- **Anthropic upstream changes**: Anthropic controls the OAuth and API surface. A breaking change upstream can invalidate this implementation without codex-lb changing. Mitigation: monitor release notes and verify in CI against mock OAuth endpoints.
- **Email nullable for Claude**: `accounts.email` becomes nullable because Anthropic OAuth does not always expose an email claim. The existing `UNIQUE(email)` constraint for Codex accounts remains. A partial unique index `(provider, claude_account_uuid)` covers Claude uniqueness instead.

## Architecture

### New module layout