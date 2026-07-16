# Tasks: reduce-settings-surface-phase-2

- [x] 1.1 Move the four scheduler cadences to constants next to their
      builders (`app/modules/quota_planner/scheduler.py` — the `max(60, ...)`
      clamp becomes moot and is dropped —,
      `app/modules/automations/scheduler.py`,
      `app/core/openai/model_refresh_scheduler.py`,
      `app/modules/sticky_sessions/cleanup_scheduler.py`); keep every
      `*_enabled` switch
- [x] 1.2 Delete the three `codex_fingerprint_*` fields and build the Codex
      `User-Agent` from the fixed `_FINGERPRINT_*` constants in
      `app/core/clients/proxy.py`
- [x] 1.3 Move `live_usage_write_min_interval_seconds` and
      `live_usage_queue_size` to constants in
      `app/modules/usage/live_ingest.py`; keep
      `live_usage_ingestion_enabled`
- [x] 1.4 Fix the request-log count-cache TTL at 30 s in
      `app/modules/request_logs/repository.py`; the test suite patches the
      constant to 0 via an autouse fixture
- [x] 1.5 Move circuit-breaker failure threshold and recovery timeout to
      constants in `app/core/resilience/circuit_breaker.py`; keep
      `circuit_breaker_enabled`; drop the corresponding Helm chart values
      (`configmap.yaml`, `values.yaml`, chart README)
- [x] 1.6 Derive the memory warning threshold as 80% of
      `memory_reject_threshold_mb` in
      `app/core/resilience/memory_monitor.py`; delete
      `memory_warning_threshold_mb` and simplify the `app/main.py` wiring
- [x] 1.7 Fix the images host model (`gpt-5.5`) as a constant in
      `app/modules/proxy/api.py` and the partial-images cap (3) in
      `app/core/openai/images.py`; keep `images_default_model`
- [x] 1.8 Add the fifteen phase-2 env names to `_REMOVED_SETTINGS`
      (grouped and commented per phase) so the existing startup WARN covers
      them; update the non-normative
      `openspec/specs/quota-phase-planner/context.md` mention
- [x] 2.1 Update tests that set removed fields; preserve what each test
      proves (constants patched via `monkeypatch.setattr`, settings-plumbing
      tests replaced with removed-field assertions, memory-warning
      derivation covered in `tests/unit/test_memory_monitor.py`)
- [x] 2.2 Extend `tests/unit/test_settings_trace_and_removed.py` with the
      phase-2 names (tuple count, membership, ignored env vars)
- [x] 3.1 `uv run pytest tests/unit -q`
- [x] 3.2 `uv run ruff check .` and `uv run ruff format --check .`
- [x] 3.3 `make typecheck` (ty)
- [x] 3.4 `python3 .github/scripts/check_simplicity_budgets.py`
- [x] 3.5 `openspec validate reduce-settings-surface-phase-2 --strict` and
      `openspec validate --specs`
