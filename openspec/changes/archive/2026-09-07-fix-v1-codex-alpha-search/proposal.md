# Fix standalone Codex search with a v1 base URL

## Why

Production requests to `POST /v1/alpha/search` return a local HTTP 405 because
standalone Codex search is registered only under `/backend-api/codex`. Clients
using the supported `/v1` base URL can generate responses but cannot search.

## What Changes

- Register `/v1/alpha/search` on the existing standalone search handler.
- Preserve the same authentication, account routing, capability enforcement,
  opaque payload forwarding, and error contract across both prefixes.
- Cover the public routes, existing doubled-prefix rewrite, trailing-slash
  behavior, authentication, and upstream failures with regression tests.

## Impact

- Capability: `responses-api-compat`.
- No new settings, persistence, dashboard changes, or client setup steps.
