# Forward Codex standalone web search

## Why

Codex runtime 0.144.1 sends standalone web searches to
`POST /backend-api/codex/alpha/search`. codex-lb does not register that route,
so the request reaches the application fallback and returns HTTP 405 before an
upstream account can be selected. Model-driven `web_search` calls inside the
Responses API use a different path and do not cover this client contract.

## What Changes

- Register `POST /backend-api/codex/alpha/search` on the Codex proxy router.
- Forward the request through the existing Codex control-request path so proxy
  authentication, account selection, refresh, affinity, failover, upstream
  routing, and response-header filtering remain consistent with other Codex
  control endpoints.
- Preserve the inbound body and query parameters. Successful responses retain
  the upstream status, body, and allowlisted headers; failures retain the
  existing Codex control error normalization, refresh, health, and failover
  behavior.
- Add unit and integration regressions for the public route contract.

## Issue Trace

- Fixes #1231

## Impact

- **Spec**: `responses-api-compat`
- **Behavior**: standalone Codex web search works through codex-lb instead of
  returning HTTP 405.
- **Persistence/UI**: no database, migration, configuration, or dashboard changes.
