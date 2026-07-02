## Why

codex-lb today pools ChatGPT/Codex accounts and exposes them via `/v1/*` (OpenAI-compatible) and `/backend-api/codex/*` (Codex-native). Users who also subscribe to **Claude Max / Pro / Team** plans through claude.ai have no way to load-balance those subscriptions — they sit alongside Codex accounts on the same operator's machine but cannot be used through the proxy.

The Claude Code CLI already authenticates against the Anthropic API using OAuth tokens issued by claude.ai. codex-lb should accept those tokens, refresh them on schedule, and forward client requests through them so that downstream clients can use Claude models with the same operational benefits the proxy already provides for Codex (dashboard, request logs, rate-limit visibility, API-key auth, sticky sessions).

## What Changes

- Add a `provider` column discriminator to the `accounts` table (`'codex' | 'claude'`) so existing account infrastructure (load balancer, sticky sessions, account-cache, request logs, dashboard cards) can carry Claude accounts alongside Codex accounts without duplicating infrastructure.
- Add a `ClaudeAuthManager` that owns the Claude-specific lifecycle: encrypt and persist OAuth tokens, schedule background refresh, handle 401-driven rotate-and-retry at request time.
- Add a passthrough `ClaudeProxyService` that forwards Anthropic-native request and response bytes between the client and `api.anthropic.com` (or the Claude-Code-authenticated equivalent) for `/claude/v1/messages` and `/claude/v1/models`, with no OpenAI ↔ Anthropic translation.
- Add a parallel `/claude/*` URL namespace separate from `/v1/*` so an API key chooses the provider pool by URL, not by model name.
- Add a `provider_scope` field to `api_keys` (CSV: `'codex'`, `'claude'`, or `'codex,claude'`) so API keys are explicitly authorized for one or both URL namespaces.
- Parse Anthropic rate-limit response headers (`anthropic-ratelimit-*-*`) and Anthropic response-body `usage` to drive existing usage/quota tracking so Claude accounts appear on the same dashboards as Codex accounts.
- Add a minimal "Claude Accounts" tab in the dashboard: list / add / disable / re-enable / soft-delete; read-only usage card showing current rate-limit headers.

## Capabilities

### New Capabilities

- `claude-oauth-pool`: Pool Claude Max/Pro/Team OAuth tokens as proxy accounts. Manual token paste (PKCE deferred to a follow-up change). Passthrough `/claude/v1/*` routes, separate from the Codex pool.

### Modified Capabilities

- `account-routing`: Account model gains a `provider` discriminator; load balancer and account-cache learn to filter by provider; existing Codex flow unchanged.
- `database-migrations`: Single Alembic revision adds `provider` to `accounts`, `provider_scope` to `api_keys`, plus Claude-specific nullable columns on `accounts`. Includes downgrade and backfill.
- `api-keys`: `provider_scope` field on creation/update/list responses; proxy middleware enforces that request URL provider matches key's provider_scope.
- `proxy-runtime-observability`: New `codex_lb_claude_*` Prometheus metric namespace when metrics are enabled.

## Impact

- **Database**: One Alembic revision extending `accounts` and `api_keys`. Existing rows backfill `provider='codex'` and `provider_scope='codex'`. Single-head upgrade path maintained. Downgrade restores prior schema.
- **Backend Python**: New module `app/modules/claude/` mirroring the shape of existing `app/modules/<feature>/` modules; new package `app/core/clients/anthropic/` for OAuth + chat clients. No edits to `ProxyService` god object (ADR-0001 preserved).
- **Frontend**: New directory `frontend/src/components/claude/` with three React components (list, add dialog, usage card) plus i18n strings for en + zh-CN.
- **Existing API surface**: `/v1/*` and `/backend-api/codex/*` are untouched. New routes are additive under `/claude/v1/*`. API keys gain a new optional field with a default that preserves existing behavior (key authorized for Codex only).
- **No breaking changes**: Defaults preserve existing behavior; clients keep working against `/v1/*` unchanged.
- **Verification**: Anthropic's Claude-Code-OAuth refresh endpoint URL, exact required auth headers (`anthropic-version`, `anthropic-beta`, etc.), and refresh-token rotation behavior are blocking unknowns that must be verified during the implementation phase.