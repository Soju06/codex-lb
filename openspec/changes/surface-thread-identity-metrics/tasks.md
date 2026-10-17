# Tasks

## 1. Implementation

- [x] 1.1 Extract `normalized_thread_key_expr` from `conversation_id_expr`
      (`app/modules/accounts/usage_time_rollup.py`) so `session_id` normalizes
      through the same SQL, and name `SUCCESS_STATUS` in
      `app/core/usage/logs.py`.
- [x] 1.2 Add `app/modules/reports/thread_identity.py`: window-bounded scope
      subquery, one per-request pass (LAG over the thread partition) and one
      per-thread pass (`count(DISTINCT account_id)`), with the 7-day ceiling,
      3-request conversation floor, 600 s turn gap and 5,000-token cache floor
      as module constants.
- [x] 1.3 Wire `ReportsRepository.aggregate_thread_identity`,
      `ReportsService.get_thread_identity` (ratio derivation, `available=false`
      beyond the ceiling) and `GET /api/reports/thread-identity`.
- [x] 1.4 Generalize `ReportCache` to `ReportCache[K, T]` and add a 300 s
      `thread_identity` cache to `ReportsCaches`.
- [x] 1.5 Frontend: zod schema, `getThreadIdentity`, `useThreadIdentity` gated
      on card visibility, `ThreadIdentityCard`, Reports page wiring, and the
      `threadIdentity` entry in `REPORT_CHART_DEFINITIONS`; en / ko / zh-CN keys.

## 2. Verification

- [x] 2.1 Query-layer unit tests (`tests/unit/test_thread_identity_metrics.py`):
      conversation and session keys, the 3-request floor, soft-deleted rows,
      warm-up exclusion, turn qualification (gap, shrinking prefix,
      unattributed endpoint, unrecorded prefix size), one thread keyed by
      either identifier column, switch counting, unkeyed reconstruction per API
      key including a row with no API key, the cache-ratio sample filter,
      window bounds, the unattributed-request counter, service ratio
      derivation and the window cap, and the report timezone.
- [x] 2.2 Integration tests (`tests/integration/test_reports_thread_identity_api.py`):
      payload shape for a keyed/unkeyed split, `available: false` beyond the
      ceiling, and a 400 on an inverted range.
- [x] 2.3 Frontend tests: `thread-identity-card.test.tsx` (baseline figures,
      approximation disclosure, detached-attribution disclosure and its
      absence, sub-0.1% formatting, unavailable state) and a Reports page test
      proving the query is only issued while the card is visible.
- [x] 2.4 `EXPLAIN (ANALYZE, BUFFERS)` for both statements against the
      production 7.2M-row `request_logs`, recorded in the PR body.
- [x] 2.5 `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
      frontend `typecheck` / `lint` / `test`, and
      `openspec validate surface-thread-identity-metrics --strict`.
