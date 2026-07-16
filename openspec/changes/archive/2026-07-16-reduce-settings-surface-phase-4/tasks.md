# Tasks: reduce-settings-surface-phase-4

- [x] 1.1 Delete the three canary settings
      (`http_responses_session_bridge_codex_prewarm_canary_percent`,
      `..._prewarm_allow_api_key_ids`, `..._prewarm_deny_api_key_ids`) and
      their `_normalize_http_bridge_prewarm_api_key_ids` validator from
      `app/core/config/settings.py`; keep
      `http_responses_session_bridge_codex_prewarm_enabled`
- [x] 1.2 Replace `_http_bridge_prewarm_canary_bucket` (and the now-dead
      eligibility-reason / input-size helpers) with
      `_http_bridge_prewarm_enabled` in
      `app/modules/proxy/_service/http_bridge/helpers.py`; simplify
      `_maybe_prewarm_http_bridge_session` so eligibility is the enabled
      flag alone (no bucket/cohort plumbing, no `canary_miss` path); update
      the `app/modules/proxy/service.py` re-export
- [x] 1.3 Drop the cohort/bucket observability dimensions: the
      `codex_lb_http_bridge_prewarm_total` counter is labelled by `outcome`
      only; remove `prewarm_canary_bucket` / `prewarm_eligible_reason` from
      the request state and request-log write plumbing. The `RequestLog`
      columns stay declared (deprecated, unwritten) for one release so old
      replicas keep inserting safely during rolling upgrades; the Alembic
      drop revision ships in the next release
- [x] 1.3b Ship every `dashboards/*.json` in the Helm Grafana ConfigMap
      (previously only `codex-lb.json`), so the updated TTFT dashboard
      actually reaches chart operators
- [x] 1.4 Add the three phase-4 env names to `_REMOVED_SETTINGS`
      (grouped and commented per phase, at the end of the tuple)
- [x] 2.1 Convert the canary sampling/cohort unit tests into tests of the
      simplified eligibility (enabled on/off only); keep every prewarm
      lifecycle test green; update request-log repository and upstream
      transport observability tests that carried the removed fields
- [x] 2.2 Extend `tests/unit/test_settings_trace_and_removed.py` with the
      phase-4 names (tuple count, membership, ignored env vars, surviving
      `prewarm_enabled` flag)
- [x] 3.1 `uv run pytest tests/unit -q`
- [x] 3.2 `uv run pytest tests/integration/test_migrations.py tests/integration/test_http_responses_bridge.py -q`
- [x] 3.3 `uv run ruff check .` and `uv run ruff format --check .`
- [x] 3.4 `make typecheck` (ty)
- [x] 3.5 `python3 .github/scripts/check_simplicity_budgets.py`
- [x] 3.6 `openspec validate reduce-settings-surface-phase-4 --strict` and
      `openspec validate --specs`
