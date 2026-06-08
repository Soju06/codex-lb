# query-caching Specification

## Purpose

Define query caching and quota-key normalization contracts so selection and dashboard reads remain fast and consistent.
## Requirements
### Requirement: Additional usage persistence normalizes upstream aliases to canonical quota keys
Persisted additional-usage rows MUST record one internal canonical `quota_key` even when upstream changes raw `limit_name` or `metered_feature` aliases.

#### Scenario: Legacy stored quota keys remain readable under the current canonical key
- **GIVEN** the registry renames a canonical additional-usage `quota_key`
- **AND** it lists the previous durable key as a legacy `quota_key` alias for that same quota family
- **WHEN** selection, dashboard, or cleanup code reads or deletes persisted rows for the current canonical key
- **THEN** rows stored under the legacy `quota_key` remain readable through the current canonical key
- **AND** canonical list/read results surface the current key instead of the legacy durable alias

#### Scenario: Refresh coalesces mixed aliases for one canonical quota before pruning
- **GIVEN** one refresh payload includes multiple `additional_rate_limits` items that resolve to the same canonical `quota_key`
- **AND** at least one alias reports usable window data while another alias for that same `quota_key` reports `rate_limit = null`
- **WHEN** the refresh persists additional usage
- **THEN** it merges all aliases by canonical `quota_key` before deleting stale rows
- **AND** persisted rows for the usable window remain available for later gated-model selection

#### Scenario: Historical rows remain readable after canonical key rename
- **GIVEN** persisted `additional_usage_history` rows were written under an earlier canonical `quota_key`
- **AND** the current registry still recognizes the same raw upstream aliases for that quota family
- **WHEN** selection or dashboard queries request the current canonical `quota_key`
- **THEN** repository reads match both the current `quota_key` and the known raw alias fields
- **AND** the historical rows remain visible until refresh rewrites them under the newer canonical key

### Requirement: Dashboard overview memoizes per-account depletion EWMA state
`GET /api/dashboard/overview` MUST cache per-account EWMA depletion state in memory so repeated polls do not re-walk the full in-window `usage_history` slice in the depletion cache check when its content is unchanged.

#### Scenario: Repeated polls with unchanged history reuse cached EWMA state
- **GIVEN** the dashboard service has previously computed depletion for an account
- **AND** a subsequent request supplies the same in-window history slice for that account with the same attached compact content signature
- **WHEN** depletion is recomputed for the dashboard response
- **THEN** the service MUST reuse the cached EWMA state for that account instead of replaying every history row
- **AND** the depletion metrics for that account MUST match the previously returned values for rate-bearing fields
- **AND** the cache hit check MUST use bounded signature metadata rather than building or retaining a per-row signature tuple
- **AND** the service MUST prune cached depletion state for account/window keys that are absent from the current dashboard history set

#### Scenario: Memoized EWMA state is invalidated when a new usage row is appended
- **WHEN** a later dashboard request supplies the same account's in-window history with an additional row appended (a new `recorded_at` past the previous latest)
- **THEN** the service MUST rebuild the EWMA state from the new history slice
- **AND** the recomputed rate MUST reflect the newly observed sample

#### Scenario: Memoized EWMA state is invalidated when an older row ages out of the window
- **WHEN** a later dashboard request supplies the same account's in-window history with the earliest row dropped (because it has aged past the window cutoff)
- **THEN** the service MUST rebuild the EWMA state from the narrowed history slice
- **AND** the cached state from the wider window MUST NOT influence the recomputed rate

#### Scenario: Memoized EWMA state is invalidated when an existing usage row is corrected
- **WHEN** a later dashboard request supplies the same account's in-window history with the same row count and endpoints but a corrected `used_percent`, `reset_at`, or `window_minutes` value on an existing row
- **THEN** the service MUST rebuild the EWMA state from the corrected history slice
- **AND** the recomputed rate-bearing metrics MUST reflect the corrected row content


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
