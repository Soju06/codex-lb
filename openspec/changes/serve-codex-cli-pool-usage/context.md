# Context

## How the Codex CLI reads limits

`codex app-server` answers `account/rateLimits/read` through
`codex-rs/backend-client`, which derives the path from the configured base
(`PathStyle::from_base_url`): a base containing `/backend-api` reads
`{base}/wham/usage`; any other base reads `{base}/api/codex/usage`. Both use
the OAuth bearer from `auth.json`, never the model-provider `base_url`, so
pointing inference at codex-lb does not move the limit read.

The CLI only attempts the read when it holds a ChatGPT-mode login; with an
API-key-only login it errors with
`codex account authentication required to read rate limits`. That is why the
route has to accept a ChatGPT bearer even though the client's inference calls
use a balancer key.

To land on `/backend-api/wham/usage`, `chatgpt_base_url` must include
`/backend-api`:

```toml
chatgpt_base_url = "https://<balancer>/backend-api"
```

A base without that segment would make the CLI call `/api/codex/usage`, which
keeps its stricter pooled-account identity check and would answer 401 for a
personal login. `chatgpt_base_url` is documented upstream as the ChatGPT login
flow override, so re-running `codex login` while it points at the balancer
will not work; log in first, then set the key.

## Identity decision and tradeoff

The user's login is a personal free ChatGPT account that is not in the pool.
`validate_codex_usage_identity` requires the `chatgpt-account-id` to resolve
to an active pooled account before any upstream call, so reusing it verbatim
would answer 401 and the feature would not work. The new route therefore uses
its own dependency:

1. `Authorization: Bearer` is required.
2. `sk-clb-...` is validated as a balancer API key and keeps the API-key view.
3. `chatgpt-account-id` is optional. When it resolves to a pooled account the
   existing account-scoped route resolution is reused; otherwise (or when the
   header is absent) direct egress is used.
4. The bearer is proved with an upstream `fetch_usage` call that does not
   retry. 200 is enough to answer; 401/403 map to 401, 429 to 429, anything
   else to 502. A success is cached for 60 s.

Tradeoff: the endpoint is not a publicly readable quota oracle, but it is
readable by any holder of a valid ChatGPT OAuth token, pooled or not. That is
intentional: the token proves "a real Codex client", and the numbers are
always the pool's aggregate, never the caller's account. The cost is one
upstream usage call the first time a credential is seen, then at most one per
minute: successes live in a process-local 60 s / 1024-entry cache keyed by a
hash of the bearer and `chatgpt-account-id`, concurrent identical proofs share
one call, failures are never cached, and the proof itself issues one request
without retries. A token revoked while its proof is still cached keeps
answering from the pool until that proof expires. The alternative (requiring
pool membership) makes the feature impossible for a personal login; the
alternative of a new opt-in setting adds a required setup step for exactly the
users this serves, which `PRINCIPLES.md` P1 forbids.

`/api/codex/usage` keeps its stricter pooled-account requirement. It is not
relaxed by this change.

## Code map

- `app/modules/proxy/api.py` — `wham_usage_router`, `wham_usage` handler,
  `backend_api_passthrough` (decode → relay → map), and
  `_build_codex_usage_payload_for_api_key` fallback/`plan_type`.
- `app/core/auth/dependencies.py` — `validate_wham_usage_identity`,
  `_prove_wham_usage_bearer`, `_pool_account_id_for_chatgpt_account`; the
  existing `validate_codex_usage_identity` is reused unchanged for pooled
  accounts.
- `app/core/auth/wham_usage_proof.py` — the 60 s success cache, its key digest
  and the per-key singleflight; never stores the raw bearer.
- `app/core/clients/backend_relay.py` — relay transport policy: header
  allowlist, reserved namespaces, Bearer check, redirect refusal and the
  upstream request.
- `app/modules/proxy/_service/rate_limit.py` — `get_rate_limit_payload`
  (payload source) and `_compute_rate_limit_headers` (response headers).
- `app/modules/proxy/helpers.py` — `_plan_type_for_accounts`,
  `_rate_limit_headers`, `_credits_headers`.
- `tests/integration/test_codex_usage_api.py` — route and payload tests.
- `tests/integration/test_backend_api_relay_routing.py` — ASGI route
  precedence between the relay and the pool-owned routes.
- `tests/unit/test_backend_api_passthrough.py` — relay transport policy and the
  route's response/error mapping.
- `tests/unit/test_wham_usage_proof.py` — proof cache, singleflight and retry
  behaviour.

## Example

Pool: one `plus` account, 5h sample at 25.78% (58/225 credits), 7d sample at
3.99% (302/7560), resets in 2h and 4d. A CLI with a personal free login calls
`GET https://<balancer>/backend-api/wham/usage`:

```json
{
  "plan_type": "plus",
  "rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 25,
      "limit_window_seconds": 18000,
      "reset_after_seconds": 7200,
      "reset_at": 1793707200
    },
    "secondary_window": {
      "used_percent": 3,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 345600,
      "reset_at": 1794057600
    },
    "monthly_window": null
  },
  "credits": null,
  "rate_limit_reset_credits": null,
  "additional_rate_limits": []
}
```

`used_percent` is truncated by the existing `_percent_to_int`; the client maps
`limit_window_seconds / 60` to `windowDurationMins` and `reset_at` to
`resetsAt`.

## Failure modes

- Upstream unreachable → 502; the CLI keeps its last snapshot.
- Upstream 429 → 429; the CLI backs off. The proof does not retry, so a 429
  costs one upstream request, not three.
- Empty pool → 200 with `plan_type: "guest"` and no windows.
- A bearer revoked while its proof is cached → 200 (pool numbers) until the
  60 s proof expires, then 401 on the next read. The window is bounded by the
  TTL, not by the token state.
- `hide_upstream_quota_from_api_keys` does not apply to ChatGPT-bearer
  callers (the setting is scoped to API keys); it does apply to `sk-clb-`
  callers.
