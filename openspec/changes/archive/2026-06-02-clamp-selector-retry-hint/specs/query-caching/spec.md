## ADDED Requirements

### Requirement: Selector retry hint is bounded by the auto-recovery window

When `select_account` cannot return a candidate, the surfaced `"Try again in {N}s"` value MUST be clamped to at most `SELECTOR_RETRY_HINT_MAX_SECONDS` (default 300). Clients reattempt within codex-lb's auto-recovery window (background `/wham/usage` refresh + per-status cooldown threshold) instead of waiting the worst-case persisted `reset_at`. The clamp affects only the user-visible string; `AccountState.reset_at` and `AccountState.cooldown_until` remain unchanged and continue to drive selection, telemetry, and dashboard reads.

#### Scenario: Quota-exceeded reset far in the future is clamped
- **GIVEN** every selectable account has `status = QUOTA_EXCEEDED`
- **AND** the soonest `reset_at` is more than `SELECTOR_RETRY_HINT_MAX_SECONDS` from now
- **WHEN** `select_account` returns `account = None`
- **THEN** the surfaced message ends with `Try again in 300s`
- **AND** the underlying `AccountState.reset_at` values are unchanged

#### Scenario: Quota-exceeded reset inside the cap surfaces the actual value
- **GIVEN** every selectable account has `status = QUOTA_EXCEEDED`
- **AND** the soonest `reset_at` is at most `SELECTOR_RETRY_HINT_MAX_SECONDS` from now
- **WHEN** `select_account` returns `account = None`
- **THEN** the surfaced message ends with `Try again in {soonest_reset_seconds}s`

#### Scenario: Cooldown_until far in the future is clamped
- **GIVEN** every account has a `cooldown_until` further than `SELECTOR_RETRY_HINT_MAX_SECONDS` from now and no `quota_exceeded` candidates exist
- **WHEN** `select_account` returns `account = None`
- **THEN** the surfaced message ends with `Try again in 300s`
