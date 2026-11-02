# Tasks

## 1. Route and identity

- [x] 1.1 Add `wham_usage_router` (`/backend-api/wham`, no router-level API-key guard) and register it in `app/main.py`
- [x] 1.2 Add `validate_wham_usage_identity` in `app/core/auth/dependencies.py`: balancer key branch, pooled-account route reuse, one upstream `fetch_usage` proof for foreign/absent `chatgpt-account-id`
- [x] 1.3 Add `GET /backend-api/wham/usage` and `/backend-api/wham/usage/` returning `RateLimitStatusPayload` from `ProxyService.get_rate_limit_payload()`
- [x] 1.4 Keep `hide_upstream_quota_from_api_keys` effective for `sk-clb-` callers

## 2. `/api/codex/usage` API-key payload

- [x] 2.1 Fall back to `_build_aggregate_credit_limits` when the key has no own limits, gated by `upstream_limits` in `usage_sections` and by the privacy toggle
- [x] 2.2 Replace `plan_type: "api_key"` with `_plan_type_for_accounts` over the pool
- [x] 2.3 Confirm the window conversion for `5h`/`7d`/`monthly` against the live `/v1/usage` rows (`5h` → 18000 s, `7d` → 604800 s, `monthly` → 2592000 s; `limit_type: "credits"` and `max_value > 0` hold)

## 3. Tests

- [x] 3.1 wham route returns the pool windows and pool plan type for a non-pool ChatGPT bearer
- [x] 3.2 wham route rejects a missing bearer
- [x] 3.3 API-key branch fills windows when the key has none, and `plan_type` is never `api_key`
- [x] 3.4 API-key branch keeps its own windows when configured
- [x] 3.5 Privacy toggle suppresses the pool fallback on `/api/codex/usage`
- [x] 3.6 `uv run pytest tests/integration/test_codex_usage_api.py` green
- [x] 3.7 `uv run ruff check` on changed files; `uv run ty check` on changed modules green

## 4. Verification

- [x] 4.0 Document the client line in `docs/client-setup.md` with a link back to `openspec/specs/api-keys/`
- [ ] 4.1 `openspec validate --specs` — CLI is not installed in this environment; artifacts were written by hand in the existing format and reviewed against `openspec/specs/api-keys/spec.md`
- [x] 4.2 Report the client-side `chatgpt_base_url` line and rollback
- [x] 4.3 Report header-path findings for `/backend-api/codex/responses`

## Notes

- `codex-rs/backend-client`'s `PathStyle::from_base_url` selects `{base}/wham/usage`
  only when the base contains `/backend-api`; a bare base reads
  `{base}/api/codex/usage`. The recommended client line therefore includes
  `/backend-api`.
- `x-codex-rate-limit-reached-type` is not emitted anywhere in the proxy today;
  the other `x-codex-*` windows/credits headers already reach API-key callers.
