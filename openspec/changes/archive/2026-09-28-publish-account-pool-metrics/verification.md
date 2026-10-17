# Verification

Base: upstream `f8ffbac2099a113fba54dfd8d77774f5bca80ffa`.

The first actual-scrape regression failed on the unchanged implementation:
`test_account_cache_refresh_populates_actual_metrics_scrape` received no account
series (`{}`) where all six status zeroes and availability zero were required.

After implementation, a normal `uv sync --dev --frozen` retains the test-only
Prometheus dependency; runtime installation still keeps it in the optional
metrics extra. The focused command passes **54 tests**:

```sh
uv run pytest tests/integration/test_account_pool_metrics.py \
  tests/integration/test_cache_invalidation_bus.py \
  tests/unit/test_metrics.py tests/unit/test_metrics_bind_guardrail.py \
  tests/unit/test_account_metrics_multiprocess.py -q --tb=short
```

The new integration coverage exercises the real Prometheus ASGI application,
including the application assembled during the production lifespan. It covers
empty/populated pools, every status, pending and physical deletion, known and
unknown expiry, expiry at the boundary without proxy traffic, failed scrapes,
disabled/absent Prometheus, and serialized overlapping refreshes preserving
local routing marks. Separate subprocesses verify real multiprocess aggregation,
newer zeroes, absence of PID labels, and dead-worker cleanup.

`make lint` passes all architecture, cancellation, timing-seam, settings-tier,
migration-topology, Ruff lint, and formatting checks. Repository-wide
`uv run ty check` passes. Strict OpenSpec 1.11.0 validation passes for this
change and all **67** main specifications.
`uv run --group docs mkdocs build --strict` and `git diff --check` also pass.

The same 54 tests pass against PostgreSQL 18 in an isolated test database. The
SQLite suite was rerun after replacing optional test skips with required imports
and again passed all 54 tests. Independent source and contribution-guide review
found no actionable issues after that test-coverage correction.

The required-import correction exposed three type errors in hosted CI: the
production `CollectorRegistryLike` protocol does not declare the `collect`
method expected by Prometheus's ASGI factory. `uv run --frozen ty check`
reproduced those same three diagnostics locally. The integration tests now
assert that the production factory returns a real `CollectorRegistry` before
passing it to Prometheus. This refines the test boundary without suppressing
checks or changing the optional production dependency. The complete
`uv run --frozen ty check` and the same 54 SQLite tests pass after this repair.

The only pytest warning is the existing Starlette `BlockingPortal` deprecation.
The full local CI gate was not run; hosted CI and current-head CodeRabbit review
remain separate merge gates. This work has not changed or queried a running
codex-lb service.
