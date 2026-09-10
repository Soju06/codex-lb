# Change: constantize-core-tunables

## Why

Twenty-seven `CODEX_LB_*` settings in the `MIGRATING` backlog of `app/core/config/tiers.py` have never been tuned by any deployment: their definition lines are unchanged since they were introduced (nine days to nine months), they are not templated by the Helm chart except two always-default keys, they appear in no `.env.example`, operator doc, issue or incident note, and the two production env files never set them. `configuration-tiers` requires every T3 setting to move to the dashboard, but a dashboard card for a value nobody changes is a switch on the screen that nobody flips (PRINCIPLES.md P2, issue #1340 precedent: "a setting the operator never needs to touch is a default in disguise"). This change removes the fields and fixes their defaults as code constants, with the same behaviour as today.

## What Changes

- **BREAKING** (documented env vars disappear; `extra="ignore"` means no deployment fails to start): the following `Settings` fields, their validators and their `SETTING_TIERS` / `MIGRATING` rows are removed and replaced by module-level `Final` constants equal to the previous defaults:
  - Upstream transport budgets: `max_sse_event_bytes` (16 MiB, `MAX_SSE_EVENT_BYTES` in `app/core/clients/proxy.py`), `upstream_response_create_max_bytes` (15 MiB, derived from the frame budget), `upstream_compact_timeout_seconds` (no constant: the dashboard `compact_request_budget_seconds` is the only total cap, pushed as a per-request override).
  - Auth and token refresh: `oauth_timeout_seconds` (30 s, `app/core/clients/oauth.py`), `token_refresh_timeout_seconds` (8 s, `app/core/auth/refresh.py`), `token_refresh_claim_ttl_seconds` (`max(30 s, admission wait + 2 x refresh timeout)` helper in `app/modules/accounts/auth_manager.py`, replacing the model validator), `proxy_refresh_failure_cooldown_seconds` (5 s, `auth_manager.py`), `proxy_admission_wait_timeout_seconds` (10 s, `app/modules/proxy/work_admission.py`).
  - Usage polling: `usage_fetch_timeout_seconds` (10 s) and `usage_fetch_max_retries` (2) in `app/core/clients/usage.py`; `usage_refresh_interval_seconds` (60 s) with the derived 180 s freshness horizon in the new leaf `app/core/usage/refresh_policy.py` (one source for the scheduler slice, the updater freshness window, the rate-limit header cache TTL and the dashboard weekly-pace horizon); `usage_refresh_auth_failure_cooldown_seconds` (300 s, `app/modules/usage/updater.py`); `usage_refresh_enabled` and `live_usage_ingestion_enabled` (always on; the request-path refresh gates and the `ignore_refresh_disabled` plumbing are deleted); `rate_limit_reset_credits_refresh_interval_seconds` (60 s, `app/core/usage/reset_credits_refresh_scheduler.py`).
  - Scheduler toggles: `sticky_session_cleanup_enabled`, `model_registry_enabled` (always on; the `main.py` registry guards and the timeout-invariant rule skip are deleted), `quota_planner_scheduler_enabled` (folded into the existing dashboard `quota_planner_settings.mode == "off"`, which was already the only switch that stopped planning; no new column).
  - Ingress, images, models: `max_decompressed_body_bytes` (32 MiB) and `max_decompressed_responses_body_bytes` (128 MiB) in the new leaf `app/core/ingress_limits.py`, imported by both the request-body guard and `cli.py --ws-max-size` so the parity is enforced in code; `image_inline_fetch_enabled` (always on, four disabled branches deleted) and `image_inline_allowed_hosts` (branch and validator deleted; the SSRF guards on scheme, literal hosts and disallowed IPs are unchanged); `images_default_model` (`DEFAULT_PUBLIC_IMAGE_MODEL = "gpt-image-2"` in `app/core/openai/images.py`); `openai_prompt_cache_key_derivation_enabled` (always on).
  - Process admission gates: `proxy_token_refresh_limit` (64), `proxy_upstream_websocket_connect_limit` (128), `proxy_compact_response_create_limit` (64) in `work_admission.py`; every gate is always created. `proxy_response_create_limit` (256) stays a setting (verdict `keep_env_T1`).
- The 27 env names join `_REMOVED_SETTINGS` for their one-release startup warning; `warn_removed_settings` is unchanged.
- Test seams are kept: every constant is a module attribute tests monkeypatch, and the scheduler dataclasses / `WorkAdmissionController` keep their constructor kwargs. Tests that used `CODEX_LB_USAGE_REFRESH_ENABLED=false` to keep request-path refreshes away from the unreachable test upstream now use an autouse fixture (`tests/conftest.py`) with a `usage_refresh_request_path` opt-out marker.
- `app/core/timeout_invariants.py`: the admission-wait operand becomes a constant `_expr` (same pattern as the model-registry refresh interval); the `model_registry_enabled` rule skip is deleted.
- Helm: `config.promptCacheKeyDerivationEnabled` and `config.stickySessionCleanupEnabled` and their configmap lines are removed so a default install does not trip its own removal warning.
- Generated settings reference regenerated; `[settings_fields].max` 130 -> 103.

### Deviation from the plan

`token_refresh_interval_days` (planned as the 28th field) is **kept**: `scripts/traffic_analysis/fast_canary_suite.py` sets `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS=365` in the controlled failure-matrix subprocess environment to suppress proactive token refresh during the canary run. Constantizing it would silently defeat that harness (the variable would be ignored with a warning). It stays a T3 `MIGRATING` setting until the canary suite gets a different seam.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: MODIFIED "Upstream Responses event size budget" (fixed 16 MiB / derived 15 MiB), "Codex compact requests are bounded by the proxy request budget" (no separate upstream compact timeout), "Responses HTTP ingress uses the expanded bounded budget" (fixed 32 MiB / 128 MiB constants, shared with `--ws-max-size`); REMOVED "Proxy-generated prompt cache key derivation is operator-toggleable".
- `live-usage-ingestion`: REMOVED "Live ingestion is decoupled and switchable", ADDED "Live ingestion is decoupled and always on".
- `rate-limit-reset-credits`: MODIFIED "Reset credits are polled per account on a fixed cadence" (60 s constant); REMOVED "Reset credit polling interval is configurable", ADDED "Reset credit polling can be disabled" (the enable toggle text moves verbatim; `rate_limit_reset_credits_refresh_enabled` itself is out of scope here and migrates to the dashboard in a later change).
- `images-api-compat`: MODIFIED "OpenAI-compatible image generation endpoint" (fixed default model).
- `http-ingress-limits`: MODIFIED "HTTP ingress reuses existing budgets" (fixed budgets).
- `proxy-admission-control`: MODIFIED "Expensive upstream work is admission controlled" (fixed gate sizes and wait), "Local overload reasons are stable and distinguishable" and "HTTP bridge startup admission waits are bounded" (fixed 10 s wait).
- `usage-refresh-policy`: MODIFIED "Background usage refresh is staggered across accounts" (fixed 60 s interval, no enable switch) and "Cross-replica token refresh serialization" (the claim TTL is the fixed helper `max(30 s, admission wait + 2 x refresh timeout)`; the derive-or-reject settings scenario becomes a fixed-TTL scenario).
- `outbound-http-clients`: MODIFIED "Upstream SSE framing scans each byte a bounded number of times" (fixed 16 MiB cap).
- `proxy-runtime-observability`: MODIFIED "HTTP bridge startup wait timeouts are logged" (fixed 10 s admission wait).
- `deployment-installation`: MODIFIED "Removed tunables are fixed constants, derived values, or dashboard settings" (this batch added to the fixed list). `context.md` notes reversing the #1340 "stays" decisions for `CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS` and `CODEX_LB_IMAGES_DEFAULT_MODEL`.
- `quota-phase-planner` (`context.md`) and `responses-api-compat` (`ops.md`): prose updated (planner switch is the dashboard mode; the websocket ingress default names the constant).

## Impact

- Code: `app/core/config/{settings,tiers}.py`, `app/core/clients/{proxy,proxy_websocket,oauth,usage,rate_limit_reset_credits}.py`, `app/core/auth/refresh.py`, `app/core/middleware/request_body_limit.py`, new `app/core/ingress_limits.py` and `app/core/usage/refresh_policy.py`, `app/core/usage/{refresh_scheduler,reset_credits_refresh_scheduler}.py`, `app/core/openai/{images,model_refresh_scheduler}.py`, `app/core/timeout_invariants.py`, `app/cli.py`, `app/main.py`, `app/modules/accounts/{auth_manager,mappers,service}.py`, `app/modules/dashboard/service.py`, `app/modules/proxy/{service,work_admission,affinity,api,images_service,load_balancer,rate_limit_cache}.py` and `_service/*`, `app/modules/quota_planner/scheduler.py`, `app/modules/sticky_sessions/cleanup_scheduler.py`, `app/modules/telemetry/snapshot.py`, `app/modules/usage/{live_ingest,updater}.py`.
- No schema change, no API change, no default change. Operators who still set one of the 27 variables get one startup warning naming it; the behaviour is the previous default in every case.
- Stacked on `slop/k0-harness-env-independence` (#2250), which replaced the test harness's `CODEX_LB_*_ENABLED=false` gating with fixture seams so the removed toggles cannot silently re-enable background loops under test.

Part of the slop-removal campaign 0908 (MIGRATING triage K1; K2 constantizes the never-tuned HTTP session bridge tunables).
