# Context: adaptive-rate-limit-windows

## Upstream policy event

- 2026-07-12 (~18:00 UTC): OpenAI announced a **temporary** removal of the 5-hour Codex usage limit for Plus, Business, and Pro plans, together with a one-time usage reset. Weekly limits remain in force. No end date was stated; official pricing docs still describe the 5-hour window as of 2026-07-13. Source: @thsottiaux announcement on X (tweet id 2076365965915467978); corroborated by BleepingComputer coverage.
- Upstream `openai/codex` stopped hardcoding the 5h/weekly window pair in PR #22929 (merged 2026-05-19): window kinds are derived from server-reported `window_minutes` at display time, and `RateLimitSnapshot.primary`/`secondary` are both optional. Codex clients tolerate any window shape the backend reports.

Because the removal is explicitly temporary, codex-lb must not delete 5h code paths. The design goal is upstream parity: derive behavior from the windows actually observed in `/backend-api/wham/usage`, and degrade cleanly when the short window is absent — while continuing to work unchanged if it returns.

## Key design decisions

- **Expired samples zero to `0.0`, not `None`**: `apply_usage_quota`'s `RATE_LIMITED -> ACTIVE` recovery branch only runs when `primary_used is not None` (and the `QUOTA_EXCEEDED` branch when `secondary_used is not None`). Mapping expired samples to unknown would freeze blocked accounts; `0.0` matches the existing `>= 100%` zeroing rule and the `select_account` auto-recovery semantics.
- **The elapsed-reset rule runs after the weekly-primary remap and mutates only derived locals**: `should_use_weekly_primary` uses `reset_at` as a tiebreaker and several guards (`long_window_quota_available`, the `QUOTA_EXCEEDED` early-clear, credit extraction) read the raw entries. Zeroing entry objects or reordering the steps would change those guards.
- **Generalized zeroing assumes an active refresh loop**: the usage refresh scheduler revisits each account roughly once per `usage_refresh_interval_seconds`, and an elapsed-reset row is already spec-stale, so a wrong optimistic `0.0` self-corrects within one refresh cycle. Deployments running with `usage_refresh_enabled=false` are out of scope for this behavior.
- **Dashboard divergence is intentional**: `app/modules/accounts/mappers.py` keeps showing raw observed samples (including elapsed reset timestamps). Operators see what upstream last reported; routing sees expired windows as reset. Aligning the dashboard is deliberately deferred to a follow-up so this change stays selection/refresh-scoped.
- **Default routing strategy stays `capacity_weighted`**: an earlier draft proposed switching the fresh-install default to `relative_availability` for the weekly-window regime. Verification refuted the rationale: the shipped default is `capacity_weighted` combined with `prefer_earlier_reset_accounts=true` (secondary window), which already hard-filters to the earliest weekly-reset day bucket before credit weighting — i.e. the incumbent default is already reset-aware. `relative_availability` additionally ignores the `prefer_earlier_reset_accounts` toggle and its min-weight cutoff (10% of best, power 2) frequently collapses heterogeneous pools to a single candidate. Operators who want continuous credits/time-to-reset scoring can select `relative_availability` explicitly.

## Verified-unaffected surfaces (checked during design review)

- Per-account usage refresh cadence stays ~`usage_refresh_interval_seconds` (the scheduler visits one account per tick with tick = interval / account count).
- `select_account`'s own `RATE_LIMITED`/`QUOTA_EXCEEDED` auto-recovery (status-gated on `state.reset_at`) is untouched and remains the live-path recovery.
- The staggered idle limit warm-up already requires a current-cycle, non-weekly primary row with a future reset, so absent or stale primary data cannot trigger warm-up probes today; only its hardcoded 300-minute duration needs generalization (follow-up change).
- Weekly-only payload remapping (`should_use_weekly_primary` / `normalize_weekly_only_rows`) is duration-gated, not plan-gated, so paid plans that start reporting a lone weekly window in the primary slot are already remapped.
- The depletion estimator is window-agnostic (driven by `seconds_until_reset`).

## Known remaining degradations (deliberate follow-ups)

- Quota phase planner still assumes 5h phases (`FIVE_HOUR_WINDOW_SECONDS`); with no short window it plans no-value warm-ups only in `auto` mode with synthetic traffic enabled and a non-zero warm-up credit budget (all default-off). Follow-up: duration generalization + plannability gating.
- Dashboard account summaries and api-keys pooled primary-credit fields keep showing the last observed 5h sample; cosmetic while the removal lasts.
