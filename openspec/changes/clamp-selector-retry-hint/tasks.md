## 1. Implement clamp helper

- [ ] 1.1 Add `SELECTOR_RETRY_HINT_MAX_SECONDS = 300` constant in `app/core/balancer/logic.py`, near `QUOTA_EXCEEDED_COOLDOWN_SECONDS` / `RATE_LIMITED_COOLDOWN_SECONDS`.
- [ ] 1.2 Add a private `_format_retry_hint(wait_seconds: float) -> str` helper that returns `f"Rate limit exceeded. Try again in {min(max(0.0, wait_seconds), float(SELECTOR_RETRY_HINT_MAX_SECONDS)):.0f}s"`.
- [ ] 1.3 Replace both `f"Rate limit exceeded. Try again in {wait_seconds:.0f}s"` formatters in `select_account` (currently at the `quota_exceeded` branch and the `cooldown_until` branch) with calls to `_format_retry_hint(wait_seconds)`.
- [ ] 1.4 Leave `AccountState.reset_at` / `AccountState.cooldown_until` and all selector decision logic untouched.

## 2. Tests

- [ ] 2.1 Add `tests/unit/test_load_balancer.py::test_select_account_caps_quota_exceeded_retry_hint`: build two `quota_exceeded` states with `reset_at = now + 89_872` and assert the surfaced message ends with `Try again in 300s`, not `Try again in 89872s`.
- [ ] 2.2 Add `tests/unit/test_load_balancer.py::test_select_account_preserves_short_quota_exceeded_retry_hint`: build a `quota_exceeded` state with `reset_at = now + 60` and assert the surfaced message ends with `Try again in 60s` (no clamp applied).
- [ ] 2.3 Add `tests/unit/test_load_balancer.py::test_select_account_caps_cooldown_retry_hint`: build a state with `cooldown_until = now + 86_400` and assert the surfaced message ends with `Try again in 300s`.
- [ ] 2.4 Confirm the existing `test_select_account_reports_cooldown_wait_time` still passes (its 30s/60s cooldowns are below the cap).

## 3. Spec + validation

- [ ] 3.1 Add the new requirement under the `query-caching` capability spec (delta at `openspec/changes/clamp-selector-retry-hint/specs/query-caching/spec.md`) covering the clamp scenarios.
- [ ] 3.2 Run `uv run pytest tests/unit/test_load_balancer.py -k 'select_account or retry_hint'` and confirm clean.
- [ ] 3.3 Run `openspec validate --specs` and confirm clean.
- [ ] 3.4 Run `uv run ruff check app/core/balancer/logic.py tests/unit/test_load_balancer.py` and confirm clean.
