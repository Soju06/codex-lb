## 1. Settings surface
- [x] 1.1 Remove `request_log_retention_days`, `usage_history_retention_days`, `http_downstream_transport_policy`, `openai_cache_affinity_max_age_seconds`, `warmup_model`, `http_responses_session_bridge_gateway_safe_mode`, `workers_per_instance` and their validators from `Settings`.
- [x] 1.2 Replace the `workers_per_instance` validator with an environment guard that rejects `CODEX_LB_WORKERS_PER_INSTANCE` other than `1` with the same error message.
- [x] 1.3 Rotate `_REMOVED_SETTINGS`: drop the phase 1-4 names, add the six names removed here; keep `warn_removed_settings`.
- [x] 1.4 Match `CODEX_LB_WORKERS_PER_INSTANCE` and the removed names case-insensitively (pydantic-settings parity) so a lowercase declaration is neither a guard bypass nor silently unreported.

## 2. Consumers
- [x] 2.1 Retention: `settings/service.py` effective values and `core/retention/job.py` resolve NULL to disabled (0); no env read.
- [x] 2.2 First-boot seed in `settings/repository.py` relies on the column defaults for the three dashboard-owned columns.
- [x] 2.3 Drop the unreachable env fallback in `_effective_http_downstream_transport_policy`.
- [x] 2.4 Make the `20260310_120000` affinity-TTL backfill inert to `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS` so a fresh-database bootstrap cannot be steered by a variable startup reports as ignored (migration-path regression test).

## 3. Docs, chart, reference
- [x] 3.1 Generator: remove the deprecated-aliases list, add the `CODEX_LB_WORKERS_PER_INSTANCE` guard note, regenerate `docs/reference/settings.md`; lower the ratchet to 128.
- [x] 3.2 Helm: remove `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS` / `config.cacheAffinityMaxAgeSeconds`.
- [x] 3.3 `docs/deployment/kubernetes.md` and the dashboard retention hint copy no longer refer to an environment value.

## 4. Verification
- [x] 4.1 Update unit tests (removed-settings warning, multi-replica guard, settings service, reference ratchet) and integration tests (data retention, settings API, warmup) to the dashboard-only contract.
- [x] 4.2 `make lint`, `uv run pytest tests/unit -q -x`, targeted integration tests, simplicity budgets, frontend lint/typecheck/test, `openspec validate --specs` and `openspec validate remove-dead-env-settings`.
