## Why

Codex fetches its rate-limit display from `chatgpt_base_url`, which defaults
to `https://chatgpt.com/backend-api/`. So by default it shows the quota of the
ChatGPT account it is logged in as, not the quota of the pool codex-lb actually
routes to. Pointing `chatgpt_base_url` at codex-lb would fix the display, but
Codex also sends its other ChatGPT-backend calls there (account checks, user
settings, plugins, cloud tasks), and codex-lb answers them with 404/405.
Operators have to choose between an accurate quota display and working Codex
features.

## What Changes

- The supported client setting is
  `chatgpt_base_url = "https://<codex-lb>/backend-api"`. In that style, every
  path Codex sends is exactly its chatgpt.com path (captured from Codex
  0.157.0), so no path rewriting is needed.
- codex-lb serves pooled usage at `GET /backend-api/wham/usage`, the path Codex
  calls in this style, with the same payload and auth as `/api/codex/usage`.
  `POST /backend-api/wham/rate-limit-reset-credits/consume` mirrors the existing
  `/api/codex/...` consume route the same way.
- codex-lb forwards any other `/backend-api/<rest>` request it does not serve
  to `<upstream>/backend-api/<rest>`, unchanged.
  - Unserved paths under `/backend-api/codex/`, `files`, and `transcribe` are
    not forwarded. Those namespaces carry pool-routed traffic.
- Forwarded requests use the **caller's own** ChatGPT bearer token and
  `chatgpt-account-id`, never a pool account's stored credentials. They are
  only forwarded when that `chatgpt-account-id` belongs to an active account in
  the pool, so codex-lb does not become an open relay.
- Forwarded requests leave through the same upstream egress route that
  account uses for its usage-identity check, and fail closed when that route
  cannot be resolved.
- Response bodies are streamed back as they arrive.
- Zero-config: no new setting. These paths return 404/405 today.

Known limit: Codex 0.157.0's connectors MCP client (`/backend-api/ps/mcp`)
sends no ChatGPT credentials to any host other than chatgpt.com. codex-lb
answers it `401` rather than borrowing a pool account's identity, so connectors
are unavailable while `chatgpt_base_url` points at codex-lb.

## Capabilities

### New Capabilities
- `codex-backend-passthrough`: serving pooled usage on the `/backend-api/wham`
  path and forwarding Codex's other ChatGPT-backend calls to upstream under the
  caller's own identity.

### Modified Capabilities
<!-- None: existing /api/codex/usage and reset-credit contracts are unchanged. -->

## Impact

- `app/modules/proxy/codex_backend_passthrough.py` (new): the
  `/backend-api/wham/usage` and consume aliases, plus the catch-all route, on
  one router included in `app/main.py` after every other `/backend-api` router.
- `app/modules/proxy/api.py`: the existing `/api/codex/usage` and consume
  handlers are reused by the new aliases, with no behavior change.
- `app/core/auth/dependencies.py`: the usage-identity verification is split
  out of `validate_codex_usage_identity` and shared with the passthrough, which
  caches a confirmed token/account binding for 60 seconds.
- `app/core/clients/codex_backend.py` (new): a streaming upstream request
  helper; the existing `codex_control_request` buffers bodies.
- Helm/Gateway: none. `/backend-api/wham` is already in the documented
  unfiltered API rule (`deployment-networking`).
- Docs: `docs/client-setup.md` gains a section on pointing Codex's
  `chatgpt_base_url` at codex-lb, linked to the new spec.
