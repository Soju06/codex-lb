## 1. Regression coverage

- [x] 1.1 Replace `test_load_selection_inputs_parallelizes_usage_queries` with a deterministic shared-session serialization test that supplies at least one active account, yields inside each usage read, fails on overlap, and confirms primary/secondary/monthly reads complete one at a time.
- [x] 1.2 Add `tests/unit/test_proxy_rate_limit.py` with `test_rate_limit_headers_serialize_usage_reads` and `test_rate_limit_payload_serializes_usage_reads`, using one in-flight guard shared by the repository bundle and representative rows that also verify unchanged header/payload values.
- [x] 1.3 Run the two new rate-limit tests against the current gather implementation and confirm they fail specifically because a second usage operation overlaps the first.

## 2. Session-safe implementation

- [x] 2.1 Replace the primary/secondary `asyncio.gather` in `_compute_rate_limit_headers()` with ordered awaits while retaining the existing monthly and credit reads and cache behavior.
- [x] 2.2 Apply the same ordered-await change in `get_rate_limit_payload()` while retaining refresh, window normalization/expiry, credits, additional-limit aggregation, and response construction.
- [x] 2.3 Remove the now-unused `asyncio` import and run the focused tests to confirm `max_in_flight == 1`, the expected call order, and unchanged result semantics.

## 3. PostgreSQL product-path coverage

- [x] 3.1 Add `tests/integration/test_codex_usage_api.py::test_codex_usage_aggregates_windows` and `tests/integration/test_proxy_compact.py::test_proxy_compact_headers_include_monthly_only_credits` to `POSTGRES_PYTEST_TARGETS` so both payload and header paths run on asyncpg.
- [x] 3.2 Run the two focused integration tests on SQLite and `make test-postgres` against PostgreSQL; confirm neither surface reports a concurrent-operation/session error.
  - SQLite: 2 passed. PostgreSQL: the complete `make test-postgres` target passed 50/50 against an isolated PostgreSQL 14 instance.

## 4. Documentation and verification

- [x] 4.1 Update `openspec/specs/query-caching/context.md` so the no-overlap decision covers all multi-window proxy reads that share one `AsyncSession`, not only account selection.
- [x] 4.2 Run changed-file Ruff format/lint, `uv run ty check`, the focused unit/integration tests, and the repository architecture check.
  - Changed sources and tests pass Ruff, format, scoped ty, focused unit/integration coverage, and the architecture checker. The global ty run reports only four unresolved `_analytics` imports in pre-existing untracked `.codex/hooks/` files outside this change.
- [x] 4.3 Run `openspec validate serialize-rate-limit-usage-reads --strict` and `openspec validate --specs`, then verify the completed change before requesting current-head CI and Codex review.
