## Why

The project currently focuses on OpenAI-compatible routes (`/v1/responses`,
`/v1/chat/completions`) and ChatGPT account flows. Users who run Claude/
Anthropic-compatible clients and want to proxy `POST /v1/messages` through
`codex-lb` cannot do so without external tooling.

For this change we need an Anthropic-compatible route backed by the official
Claude SDK runtime path with Linux-only usage credential discovery, while
preserving dashboard value (request logs, stats, trends, and usage windows).

## What Changes

- Add Anthropic `POST /v1/messages` route implemented via local Claude SDK
  calls (no direct bridge dependency).
- Add Linux-only automatic Claude credential discovery for OAuth bearer token,
  with explicit environment override support and helper command fallback.
- Add Anthropic usage ingestion for 5h/7d windows and map these windows into
  existing usage history slots (`primary`/`secondary`) for dashboard graphs.
- Persist Anthropic requests in existing request logs with token/cost/error
  metrics, and keep API key reservation settlement behavior aligned.
- Add Anthropic pricing aliases so cost/stat charts work for Claude models.

## Non-Goals

- No multi-account Anthropic balancing.
- No Anthropic account onboarding UI.
- No OpenAI route translation on top of Anthropic (`/v1/responses`,
  `/v1/chat/completions`).

## Capabilities

### Added Capabilities

- `anthropic-messages-compat`: Anthropic-compatible `/v1/messages` behavior
  backed by Claude SDK runtime calls.

### Modified Capabilities

- `api-keys`: apply API key authentication and model restriction rules to the
  Anthropic Messages route.

## Impact

- **Code**:
  - `app/modules/anthropic/*`
  - `app/core/auth/anthropic_credentials.py`
  - `app/core/clients/anthropic_proxy.py`
  - `app/core/clients/anthropic_usage.py`
  - `app/core/config/settings.py`
  - `app/core/usage/pricing.py`
  - `app/core/usage/refresh_scheduler.py`
  - `app/dependencies.py`
  - `app/main.py`
- **Tests**:
  - new Anthropic route/unit tests
  - dashboard/request-log integration regressions for Anthropic data paths
- **Docs**:
  - `.env.example`
  - `README.md`
