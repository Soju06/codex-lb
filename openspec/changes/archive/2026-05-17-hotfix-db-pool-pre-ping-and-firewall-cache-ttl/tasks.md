# Tasks

## 1. Engine pool health

- [ ] 1.1 In `app/db/session.py::_postgres_async_engine_kwargs`, add
      `pool_pre_ping=True` and `pool_recycle=settings.database_pool_recycle_seconds`
      to the returned kwargs. SQLite paths are untouched.
- [ ] 1.2 Add `database_pool_recycle_seconds: int = Field(default=1800, gt=0)`
      to `app/core/config/settings.py` so operators can tune it.
- [ ] 1.3 Update `tests/unit/test_db_session.py` to assert the kwargs are
      forwarded for PostgreSQL URLs and absent for SQLite URLs.

## 2. Firewall IP cache TTL

- [ ] 2.1 Change `FirewallIPCache` default `ttl_seconds` from `2` to `30`
      in `app/core/middleware/firewall_cache.py`.
- [ ] 2.2 Add `firewall_ip_cache_ttl_seconds: int = Field(default=30, gt=0)`
      to settings and wire it into the module-level
      `_firewall_ip_cache = FirewallIPCache(...)` construction (read from
      settings at first access, fall back to default if settings are not
      initialised — same pattern as other lazily-resolved caches).
- [ ] 2.3 Update `tests/unit/test_hot_path_caches.py` (or add a new test)
      so the default TTL is asserted at 30 seconds and configurability is
      exercised.

## 3. Specs

- [ ] 3.1 Add delta requirement under `database-backends` (ADDED
      `### Requirement: PostgreSQL engines validate and recycle connections`).
- [ ] 3.2 Add delta requirement under `api-firewall` (ADDED
      `### Requirement: Firewall IP cache TTL is operator-configurable`).
- [ ] 3.3 Run `openspec validate hotfix-db-pool-pre-ping-and-firewall-cache-ttl`.

## 4. Validation

- [ ] 4.1 `uvx ruff check . && uvx ruff format --check .`
- [ ] 4.2 `uv run ty check` (revert any incidental `uv.lock` change).
- [ ] 4.3 `.venv/bin/python -m pytest tests/unit/test_db_session.py
      tests/unit/test_hot_path_caches.py tests/integration/test_proxy_chat_completions.py
      tests/integration/test_proxy_responses.py -q`.
- [ ] 4.4 Open PR with `Fixes #672` cover; wait for CI and `@codex review`.
