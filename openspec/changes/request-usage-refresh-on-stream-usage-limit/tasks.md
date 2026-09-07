# Tasks

## 1. Coalesced request-triggered refresh

- [x] 1.1 Add `UsageUpdater.request_refresh(account_id)` with a 15 s per-account debounce, `usage_refresh_enabled` and auth-cooldown short-circuits, and reset in `_clear_usage_refresh_state()`.
- [x] 1.2 Join a scheduler refresh already in flight on the bare account key; otherwise run on the owned-session singleflight key with `join_existing=True`, loading a fresh background row and bypassing the freshness gate; never touch a caller's `Account`.
- [x] 1.3 Record `_last_successful_refresh` and clear the auth cooldown on a successful fetch; log and swallow `Exception` only.

## 2. Streaming trigger

- [x] 2.1 Request the refresh from `_handle_stream_error` after `mark_rate_limit` when the code is `usage_limit_reached`, scheduled via `_schedule_cancel_safe_cleanup(action="request_usage_refresh")`.
- [x] 2.2 Keep `rate_limit_exceeded`, quota codes, account-neutral, model-scoped and transient failures free of refresh requests.

## 3. Reasoning-replay observability

- [x] 3.1 Define `codex_lb_upstream_reasoning_replay_400_total` with the tri-state fallback and `__all__` entry.
- [x] 3.2 Count upstream 400 (or code-less `invalid_request_error`) rejections whose message references reasoning without changing classification or account health.

## 4. Verification

- [x] 4.1 Unit coverage: storm -> single fetch, concurrent runs coalesce, joins the scheduler's in-flight refresh on the bare account key and an owned-session refresh, ignores completed scheduler tasks, logs a joined scheduler failure, debounce, fresh-row/ineligible rows, cancellation propagation, trigger and negative controls, counter predicate and no-op paths.
- [x] 4.2 Integration coverage: a streamed `usage_limit_reached` writes the >= 100 % row without a scheduler tick and the next selection reports `usage_limit_reached` with `resets_at`.
- [x] 4.3 ruff format/check, ty, `scripts/check_proxy_architecture.py`, strict OpenSpec validation.
