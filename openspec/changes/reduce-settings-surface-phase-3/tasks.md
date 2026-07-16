# Tasks: reduce-settings-surface-phase-3

- [x] 1.1 Remove the two background-pool overrides and make the
      background engine derive its pool sizing from
      `database_pool_size` / `database_max_overflow` unconditionally
      (`app/db/session.py`; the `background` branch of the engine-kwargs
      helper collapses away)
- [x] 1.2 Fix the PostgreSQL pool checkout timeout (30.0 s) and connection
      recycle window (1800 s) as `_POSTGRES_POOL_TIMEOUT_SECONDS` /
      `_POSTGRES_POOL_RECYCLE_SECONDS` constants in `app/db/session.py`;
      keep `database_pool_size` and `database_max_overflow` as settings
- [x] 1.3 Remove the six drain/probe threshold settings and stop passing
      them from `app/modules/proxy/load_balancer.py`; the
      `evaluate_health_tier` parameter defaults in
      `app/core/balancer/logic.py` (identical values) become the single
      source of truth; keep `soft_drain_enabled` and
      `deterministic_failover_enabled`
- [x] 1.4 Add the ten phase-3 env names to `_REMOVED_SETTINGS` (grouped
      and commented per phase) so the existing startup WARN covers them;
      update the non-normative pool wording in
      `openspec/specs/database-backends/context.md`
- [x] 2.1 Update tests that set removed fields; preserve what each test
      proves (engine-kwargs tests assert the fixed constants and the
      derived background sizing; `_state_from_account` drain tests
      exercise the fixed thresholds imported from
      `app.core.balancer.logic`)
- [x] 2.2 Extend `tests/unit/test_settings_trace_and_removed.py` with the
      phase-3 names (tuple count, membership, ignored env vars)
- [x] 3.1 `uv run pytest tests/unit -q` (plus the drain/failover and DB
      session test files explicitly)
- [x] 3.2 `uv run ruff check .` and `uv run ruff format --check .`
- [x] 3.3 `make typecheck` (ty)
- [x] 3.4 `python3 .github/scripts/check_simplicity_budgets.py`
- [x] 3.5 `openspec validate reduce-settings-surface-phase-3 --strict` and
      `openspec validate --specs`
