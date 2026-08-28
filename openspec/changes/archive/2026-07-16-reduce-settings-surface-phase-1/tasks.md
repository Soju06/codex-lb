# Tasks: reduce-settings-surface-phase-1

- [x] 1.1 Move the six OAuth protocol values to module constants in
      `app/core/config/settings.py` and update all call sites
      (`app/core/clients/oauth.py`, `app/core/auth/refresh.py`,
      `app/modules/oauth/service.py`)
- [x] 1.2 Hardcode the seven auth-guardian tuning values as constants in
      `app/core/auth/guardian.py`; keep `auth_guardian_enabled`
- [x] 1.3 Replace the six debug log booleans with one `CODEX_LB_TRACE`
      comma-separated channel setting plus a cached
      `Settings.trace_channels` frozenset; convert call sites in
      `app/modules/proxy/_service/observability.py` and
      `app/core/clients/proxy.py` to membership tests
- [x] 1.4 Delete the three per-class bulkhead override fields and the
      defaulting validator; derive per-class limits from
      `bulkhead_proxy_limit` via `BulkheadSemaphore` in `app/main.py`
- [x] 1.5 Move token-refresh claim wait/poll to constants in
      `app/modules/accounts/auth_manager.py`; leave
      `token_refresh_claim_ttl_seconds` and its floor validator intact
- [x] 1.6 Add `_REMOVED_SETTINGS` and `warn_removed_settings()` in
      `app/core/config/settings.py`; call it once at startup in
      `app/main.py`
- [x] 2.1 Update tests that set removed fields; preserve what each test
      proves (bulkhead derivation moved to `tests/unit/test_bulkhead.py`,
      trace tests converted to channel fakes)
- [x] 2.2 Add `tests/unit/test_settings_trace_and_removed.py`: trace-channel
      parsing/normalization/caching, removed env vars ignored, and the
      one-WARN removed-settings helper
- [x] 3.1 `uv run pytest tests/unit -q`
- [x] 3.2 `uv run ruff check .` and `uv run ruff format --check .`
- [x] 3.3 `make typecheck` (ty)
- [x] 3.4 `python3 .github/scripts/check_simplicity_budgets.py`
- [x] 3.5 `openspec validate reduce-settings-surface-phase-1 --strict` and
      `openspec validate --specs`
