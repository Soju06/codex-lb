## 1. Implementation

- [x] 1.1 Add `StickySessionKind.STICKY_THREAD` to the `budget_pressured` kind tuple in `LoadBalancer._select_with_stickiness` so `sticky_thread` mappings reallocate when the pinned account is strictly above the sticky reallocation budget threshold and a healthier candidate exists
- [x] 1.2 Update the adjacent code comment so it no longer enumerates only `prompt_cache` and `codex_session`; the rebind-on-pressure logic now applies to all sticky kinds
- [x] 1.3 Preserve no-thrash behavior when every candidate is also above the threshold (parity with the existing `prompt_cache` / `codex_session` branch)

## 2. Verification

- [x] 2.1 Add `test_budget_threshold_reallocates_sticky_thread_affinity` in `tests/unit/test_select_with_stickiness.py` (red on `main`, green with the fix)
- [x] 2.2 Add `test_sticky_thread_preserves_pinned_when_pool_also_above_threshold` (no-thrash regression guard)
- [x] 2.3 Add `test_sticky_thread_below_threshold_does_not_reallocate` (no-over-trigger regression guard)
- [x] 2.4 Run `uv run pytest tests/unit/test_select_with_stickiness.py -q` and confirm full file is green
- [ ] 2.5 Run `openspec validate --specs` and confirm clean (not run locally: `openspec` CLI not installed in this environment; deferring to repo CI)
