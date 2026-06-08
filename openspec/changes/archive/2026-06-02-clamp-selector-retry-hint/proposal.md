## Why

When the balancer cannot select any account, `select_account` in `app/core/balancer/logic.py:227-235` surfaces `"Rate limit exceeded. Try again in {wait_seconds:.0f}s"`. The `wait_seconds` is derived directly from `min(reset_at)` across the quota-exceeded pool (or `min(cooldown_until)` for the cooldown branch). When upstream `reset_at` points many hours into the future — common after OpenAI-side "team reset events" where `/wham/usage` propagates lazily, see https://github.com/Soju06/codex-lb/issues/676 and https://github.com/Soju06/codex-lb/issues/678 — clients see panic-inducing retry hints like `"Try again in 89872s"` (~25h) and back off accordingly.

In practice, codex-lb's background usage refresh runs every `usage_refresh_interval_seconds` (default 60s), and the per-status cooldowns (`QUOTA_EXCEEDED_COOLDOWN_SECONDS`, `RATE_LIMITED_COOLDOWN_SECONDS`) are 120s. Recovery typically completes well inside a 5-minute window when the upstream limiter catches up or the primary window rolls over. The current hint dishonestly suggests clients must wait the worst-case `reset_at`, which causes Codex CLI and other clients to sleep for hours when they should reattempt much sooner.

Observed 2026-05-17 during the May 16 OpenAI paid-plan reset event: all three accounts surfaced `Try again in 89872s` despite `/wham/usage` reporting `allowed=true, limit_reached=false` within minutes of the screenshot.

## What Changes

- Add `SELECTOR_RETRY_HINT_MAX_SECONDS` constant (default `300`) to `app/core/balancer/logic.py`, sitting alongside the existing cooldown constants.
- Add a private `_format_retry_hint(wait_seconds: float) -> str` helper that clamps the surfaced wait to `SELECTOR_RETRY_HINT_MAX_SECONDS` and emits `"Rate limit exceeded. Try again in {capped:.0f}s"`. The clamp only affects the user-visible string; the underlying `AccountState.reset_at` and `cooldown_until` fields are unchanged and continue to drive selection logic, telemetry, and dashboard reads.
- Replace both `f"Rate limit exceeded. Try again in {wait_seconds:.0f}s"` formatters in `select_account` with calls to `_format_retry_hint`.
- Add regression tests in `tests/unit/test_load_balancer.py` covering the clamp behavior for the `quota_exceeded` branch and the `cooldown_until` branch.

## Impact

- Clients (Codex CLI, OpenCode, OpenClaw) now see a retry hint bounded by 300s when codex-lb has zero selectable accounts. Their next retry will land inside codex-lb's auto-recovery window (60s refresh + 120s cooldown buffer + margin), so transient `/wham/usage` divergence after OpenAI reset events resolves on the next attempt instead of stalling the client for hours.
- True hard caps (multi-day weekly resets where `/wham/usage` correctly reports `limit_reached=true`) still produce repeated rejections — clients will retry, see the same 503, and back off via their own exponential backoff. Net behavior is no worse than today's repeated requests against a hard-capped account, and substantially better when the upstream state has actually recovered.
- No change to selector decision logic, account state transitions, persistence, or upstream HTTP behavior. The clamp lives entirely in the user-visible string returned alongside `SelectionResult(account=None, ...)`.
- No public API or schema change. The error envelope shape is preserved.
