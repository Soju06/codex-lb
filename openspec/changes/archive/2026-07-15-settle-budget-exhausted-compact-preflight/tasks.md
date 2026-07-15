# Tasks

- [x] 1. Create OpenSpec change artifacts (proposal, design, tasks, delta spec for `usage-refresh-policy`) and validate strictly.
- [x] 2. Settle the reservation before the before-freshness-check budget-exhausted terminal in `compact_responses` (`_service/compact.py`): call `_settle_compact_api_key_usage(response=None)` after the existing `release_account_lease` and before `_raise_proxy_budget_exhausted()`.
- [x] 3. Settle the reservation before the after-freshness-check-reserve budget-exhausted terminal (same pattern, same site cluster).
- [x] 4. Settle the reservation before the after-freshness-check budget-exhausted terminal (post-`_ensure_fresh_with_budget`, before the retry loop).
- [x] 5. Settle the reservation before the post-401 forced-refresh preflight budget-exhausted terminal inside the retry loop's 401 handler.
- [x] 6. Leave the inner `_call_compact` budget terminals unchanged (their `upstream_request_timeout` `ProxyResponseError` is already settled by the enclosing retry-loop handler before raising); confirm no double-settle.
- [x] 7. Add a route-level regression on the compact responses harness (`test_proxy_compact_preflight_budget_exhausted_settles_reservation`) driving a budget-exhausted preflight on the bridge-forwarded (`owns_reservation` false) path and asserting `_settle_compact_api_key_usage` is awaited before the 502 `upstream_request_timeout` surfaces. Confirm it FAILS without the fix and PASSES with it.
- [x] 8. Run targeted pytest, ruff check + format, `check_proxy_architecture.py`, `ty check`, and `openspec validate settle-budget-exhausted-compact-preflight --strict` + `openspec validate --specs`.
