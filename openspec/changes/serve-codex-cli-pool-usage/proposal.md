# serve-codex-cli-pool-usage

## Why

The Codex CLI reports usage limits through `codex app-server`'s
`account/rateLimits/read`, which does not read the model-provider
`base_url`. Its backend client picks the path from `chatgpt_base_url`
(`codex-rs/backend-client`'s `PathStyle`): a base containing `/backend-api`
reads `{chatgpt_base_url}/wham/usage`, any other base reads
`{chatgpt_base_url}/api/codex/usage`. Both calls use the ChatGPT login held in
`auth.json`, not the balancer key. A client such as T3 Code therefore shows
the personal account's numbers even when inference is routed through
codex-lb, and there is no supported way to point that read at the balancer.

Two defects also keep the existing compatibility route from being usable by
that client once it can be pointed at the balancer:

- `GET /api/codex/usage` accepts balancer API keys, but
  `_build_codex_usage_payload_for_api_key` builds windows only from the key's
  own limits. A key with no limits returns `rate_limit: null`, and the CLI
  then falls back to a synthetic "out of Codex messages" snapshot with a
  single 43200-minute window at 100%.
- The same builder returns `plan_type: "api_key"`, which is not a plan the
  Codex client knows (the payload enum is `guest|free|go|plus|pro|prolite|
  free_workspace|team|self_serve_business_*|business|ent26|
  enterprise_cbp_*|education|quorum|k12|enterprise|edu`).

`GET /v1/usage` already solves the first defect with
`limits = own_limits or upstream_limits`; the Codex-shaped route never got
that fallback.

## What Changes

- Add `GET /backend-api/wham/usage` (and `/backend-api/wham/usage/`) to the
  proxy, returning `RateLimitStatusPayload` built from the pool via
  `ProxyService.get_rate_limit_payload()`: the pool's aggregate primary,
  secondary and monthly windows plus the pool's plan type. The route is
  registered on its own router because the existing `wham_router` requires a
  balancer API key, which the Codex CLI does not send to this endpoint.
- Authenticate the new route with a dedicated dependency:
  - a balancer `sk-clb-...` key keeps the API-key view (`/api/codex/usage`
    parity, including `hide_upstream_quota_from_api_keys`);
  - a ChatGPT OAuth bearer is accepted after one cheap upstream
    `fetch_usage` validation, whether or not its `chatgpt-account-id` belongs
    to a pooled account. The response is always the pool's windows, never the
    caller's account windows.
- Fix the API-key branch of `/api/codex/usage`:
  - when the key has no own credit limits, fill `rate_limit` from the pool's
    aggregate credit windows (`_build_aggregate_credit_limits`), subject to
    the key's `usage_sections` containing `upstream_limits` and to
    `hide_upstream_quota_from_api_keys` being off;
  - replace `plan_type: "api_key"` with the pool plan type from
    `_plan_type_for_accounts` (falling back to `guest` for an empty pool,
    which is a valid payload plan).
- No schema, database, or setting change.
- `docs/client-setup.md` gains the `chatgpt_base_url` line and its rollback,
  linked back to `openspec/specs/api-keys/`.

## Impact

- New public path: `GET /backend-api/wham/usage` and its trailing-slash
  variant.
- `GET /api/codex/usage` API-key payloads now carry pool windows when the key
  has none, and a Codex-readable `plan_type`.
- `/backend-api/codex/responses` and the other response paths already emit
  `x-codex-primary-*`, `x-codex-secondary-*`, `x-codex-monthly-*` and
  `x-codex-credits-*`; `hide_upstream_quota_from_api_keys` suppresses them for
  API-key callers. No change needed there.
