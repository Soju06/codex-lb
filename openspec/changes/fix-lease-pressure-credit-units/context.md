# Context: fix-lease-pressure-credit-units

## Purpose

Make runtime lease pressure a spreading signal again, not a hidden "account exhausted" verdict. See `proposal.md` for the production evidence.

Purpose of the measurement section below: it records why the fix avoids the plan capacity constants and what is known about the unit OpenAI meters.

## Decision: percentage points, not a unit conversion

Options considered were a price-based conversion (lease tokens x model credit rate / plan capacity), a fixed number of points per lease, and removing the term. Fixed points was chosen:

- Removing the term would drop the leased-estimate input that the `responses-api-compat` pressure requirement keeps.
- A price-based term needs per-plan capacities. The measurement below shows codex-lb's capacity constants do not describe OpenAI's limits, so it would rest on uncalibrated numbers. It would also be negligible: a maximum-size lease on `gpt-6.1-sol` is 8,192 x 50/1M + 2,048 x 250/1M = about 0.92 OpenAI credits, about 0.34% of a Plus 5-hour window (~272 credits) and about 0.05% of a Plus weekly window (~1,940 credits).
- A second opinion (Jev, `jev-1.13.0`, given primary evidence only) split between fixed points (0.51) and removal (0.38), with price-based at 0.10.

## Measurement: what OpenAI's quota percent tracks (Plus, 2026-07-14 to 2026-10-01)

Method, scripts and raw output are reproducible from a read-only export of `request_logs` and `usage_history` into SQLite; the exact procedure is posted on #2420. Summary of what was measured:

- **Unit.** For every 5-hour window instance (161) the rise in reported percent was fitted against what the account's requests consumed in that window. Fits through the origin gave R^2 0.33 for total tokens, 0.34 for uncached tokens, and 0.905 for tokens priced at OpenAI's published per-model credit rates (https://learn.chatgpt.com/docs/pricing#token-rates). The published credit rates equal 25x the USD API prices for every listed model.
- **Plus capacity in OpenAI credits.** 5-hour window: median ~272 (IQR 249-316). Weekly window: median ~1,940 (IQR 1,705-2,078). codex-lb uses 225 and 7,560.
- **Agreement with #2420.** #2420 measured ~9,000 (Pro Lite, "5x") and ~36,000 (Pro, "20x"); 5x and 20x of ~1,800-1,900 for Plus. codex-lb's Plus and Pro Lite weekly constants are both about 4x the measured values.
- **Limit structure changed.** Until 2026-08-25 ~14:13 UTC upstream reported only a weekly window, in the `primary` slot. From then on it reported a 5-hour window in `primary` and the weekly window in `secondary`. Anything that reads the slot name instead of `window_minutes` misclassifies the July data.
- **Limits over time.** Weekly windows where estimated-rate models account for at most a quarter of the credits implied ~2,110-2,510 credits in mid-July (07-16 to 07-22) and ~1,720-2,100 in late September (09-20 to 09-26); the 5-hour window's weekly medians stayed at ~243-309 since it appeared. Two accounts cannot distinguish a drift of that size from noise. Weekly windows starting 07-25 to 07-28, made up entirely of gpt-5.6-luna/terra traffic, imply only 359-701 credits, while windows with the same all-luna/terra mix on 07-31 and 08-01 imply 1,724-1,894, so those models' rates alone do not explain the dip. gpt-5.6-luna/terra are absent from the published rate page and their API price changed on ~2026-08-30, so their July credit rate is unknown.

Constraints on the measurement:

- Two accounts, both Plus. No other plan types.
- History starts 2026-07-14; a codex-lb update in July went badly and all earlier request and usage history was lost.
- Upstream `used_percent` is an integer (1% of a Plus 5-hour window is ~2.7 OpenAI credits).
- Usage outside codex-lb is unrecorded. A check found 0.1% of all percent rises (6 of 5,091 points for 5-hour, 1 of 800 weekly) happened with no proxied requests on that account; overlapping outside usage is not detectable.
- Models absent from the published page (gpt-5.6-luna, gpt-5.6-terra, gpt-5.4*) use USD price x 25. No weekly window after 08-25 is free of them.
- Stored `request_logs.cost_usd` changed basis on 2026-08-30 for luna/terra and was not used; every request was re-priced from one table.

## Failure mode being removed

- One open stream per account takes every Plus account to 100% effective weekly usage.
- `relative_availability` loses all signal and falls back to tie-breaks that ignore real usage.
- The sticky secondary-budget gate cannot find a below-threshold target, so threads stay on a nearly exhausted account until it hits its upstream limit.

## Example

Plus capacity 7,560 credits. Account A persisted at 95%, account B at 38%. Each has one stream lease of 10,240 estimated tokens.

- Before: A = min(100, 95 + 2.5 + 135.4) = 100; B = min(100, 38 + 2.5 + 135.4) = 100. Both score 0, so the fallback tie-break decides.
- After (default lease token weight 1.0): A = 95 + 2.5 + 1.0 = 98.5; B = 38 + 2.5 + 1.0 = 41.5; both stay below 100 and B keeps the higher score and wins.

## Readers of the pressure-adjusted fields (task 1.3)

`AccountState.used_percent` / `secondary_used_percent` carry the pressure-adjusted values; the new `persisted_used_percent` / `persisted_secondary_used_percent` carry the pressure-free ones. Reviewed readers:

- Sticky budget gate (`_state_above_sticky_budget_threshold`) and `_state_above_budget_threshold`: read the adjusted values. Behavior change (intended): pressure is now at most `lease_token_weight` + the in-flight penalty points instead of up to 100, so these gates no longer fire on one open stream. A threshold of 100 can no longer be crossed by pressure: a persisted value below 99 is lifted at most to 99.0, a persisted value in [99, 100) keeps its persisted value, and persisted exhaustion (100) is preserved.
- Health tiers (`_sync_runtime_health_tier`): evaluated from persisted values before pressure is added. No change.
- Exhaustion evidence (`_usage_exhausted*`, `priority_*_used_percent`): gated on QUOTA_EXCEEDED / RATE_LIMITED status and fed from persisted values. No change.
- `relative_availability` scoring and `_usage_sort_key`: read the adjusted values, so scores stay positive under one open stream; only the zero-score fallback uses the persisted values.
- Quota planner state builds and the usage-refresh recovery reconciliation call `_state_from_account` with idle runtime state or the same tunables; they inherit the smaller pressure term. No other change.
- `capacity_credits` is still populated on `AccountState` for the capacity-weighted and relative-availability credit math; only the lease term stopped using it.

Test note: soft drain defaults to on, and it moves a 95% weekly account to the draining health tier on persisted usage alone. The integration tests disable soft drain (dashboard snapshot) so they exercise lease pressure and not the health tier.

## Operator mitigation before the fix ships

Dashboard → Settings → routing weights → lease token weight = `0`. This is the existing `proxy_account_lease_token_weight` setting (minimum 0), with no restart required.
