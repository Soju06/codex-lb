# Change: fix-lease-pressure-credit-units

## Why

Usage-based routing adds in-flight pressure to each account's persisted usage before scoring it (`_state_from_account`, `app/modules/proxy/load_balancer.py`). One of the pressure terms divides two quantities in different units:

```python
capacity_credits = usage_core.capacity_for_plan(account.plan_type, long_window_key)   # plus: 7,560 credits / week
leased_token_pressure_pct = runtime.leased_tokens * lease_token_weight / capacity_credits * 100.0
```

`leased_tokens` is the sum of per-request token estimates (`_estimated_lease_tokens_from_request_usage_budget`: up to 8,192 input + 2,048 output = 10,240 tokens). `capacity_credits` is the plan's weekly allowance in credits. One maximum-size in-flight request therefore adds 10,240 / 7,560 x 100 = 135 percentage points to a Plus account's weekly usage, which `min(100.0, ...)` turns into 100%. One open stream makes a Plus account look fully used for the week.

Under `relative_availability` the effect is a routing failure:

1. Every account with one open stream scores `remaining_credits = 0`.
2. When all candidates score zero, `_select_relative_availability` falls back to `min(available, key=_usage_sort_key)` (or the seeded variant). Every account is now tied at 100/100, so the choice comes down to `last_selected_at`, the account id, or a thread seed. The real usage difference is gone.
3. The sticky secondary-budget gate (`_state_above_sticky_budget_threshold`) sees every alternative above threshold too, so it cannot move a thread off an account that really is near exhaustion.

### Observed on a production deployment (2026-10-01)

Two Plus accounts with default dashboard settings (`relative_availability`, power 2, top-K 5, lease token weight unset = 1.0):

| Account | Weekly used | Weekly reset |
|---|---:|---|
| A | 95% | 2026-10-03 17:10Z |
| B | 38% | 2026-10-03 17:11Z |

With persisted usage alone, B scores 1.72 credits/min and A scores 0.14. A's normalized weight, (0.14/1.72)^2 ≈ 0.006, is below the 0.1 floor, so B should serve every new selection. Over the previous 24 hours A served 2,616 requests and B served 1,213. Every selection in the server log showed both accounts at zero:

```
Relative availability winner account=<B> remaining_credits=0.00 remaining_minutes=2731.51 score_per_minute=0.000000 weight=fallback
Relative availability winner account=<A> remaining_credits=0.00 remaining_minutes=2730.01 score_per_minute=0.000000 weight=fallback
```

`codex_lb_account_inflight_leases{kind="stream"}` was 1 on each account. Running the deployed `_state_from_account` against the live database gave `secondary_used_percent = 38.0 / 95.0` with no runtime pressure, and `100.0 / 100.0` with `inflight_streams = 1, leased_tokens = 10240`.

## What Changes

- **Lease pressure is a bounded number of percentage points, not a unit conversion.** The leased-token term becomes `lease_token_weight x leased_tokens / max_lease_estimate` percentage points (the maximum per-request estimate is 10,240 tokens), so one full-size lease adds `lease_token_weight` points (default 1.0) on any plan. This is the same shape as the existing in-flight penalty. It removes the token/credit unit mix, and it no longer depends on the per-plan capacity constants, which do not match measured consumption (see `context.md` and #2420).
- **In-flight pressure alone cannot exhaust an account.** Runtime pressure (in-flight creates, streams and leases) is a spreading signal. When an account's persisted usage for a window is below 100, the pressure-adjusted usage for that window stays below 100.
- **The relative-availability all-zero fallback ranks by persisted usage.** If every candidate still scores zero, the fallback orders candidates by persisted (pressure-free) secondary and primary usage before any recency, id or seed tie-break, so a real 95% vs 38% difference decides the pick.
- **Regression coverage on the failing path:** two Plus accounts with different persisted weekly usage and one open stream lease each. Selection through the load balancer must pick the lower-usage account, and the sticky reallocation path must move a thread off an account above the secondary budget threshold.

No new setting. No schema change or migration. `proxy_account_lease_token_weight` keeps its name, bounds and dashboard tri-state. Its unit changes from "tokens per credit of capacity" to "percentage points per full-size lease", and that change is called out in the release notes.

### Why points and not a price-based conversion

The measurement in `context.md` shows OpenAI's quota percent tracks model-priced consumption: tokens x each model's published credit rate. A correctly priced maximum-size lease on `gpt-6.1-sol` is 8,192 x 50/1M + 2,048 x 250/1M = about 0.92 OpenAI credits: about 0.34% of a Plus 5-hour window (~272 credits) and about 0.05% of a Plus weekly window (~1,940 credits) at the measured capacities. A price-based term would therefore be accurate but negligible. It would also need per-model rates and per-plan capacities in the balancer's lock path. Both rest on constants that #2420 and this measurement show are uncalibrated. A fixed point value keeps the burst-spreading behaviour `responses-api-compat` requires and needs no calibration.

## Related issues

- Tracked as #2554 (filed 2026-10-01). Before filing, no existing issue or PR reported this. Searches covered `lease token`, `leased_tokens`, `lease_token_weight`, `relative availability`, `pressure`, `inflight penalty`, `pinned account`, and routing-skew phrasings.
- #2420 (hard-coded Pro / Pro Lite capacities disagree with observed consumption) is adjacent: wrong capacity values would still distort the converted lease term, but they are not the cause here.
- #578 (budget-safe gate design) is adjacent: this change does not alter gate thresholds, only the inputs they read.

## Impact

- Specs: `responses-api-compat` (in-flight pressure requirement), `account-routing` (relative-availability fallback).
- Code: `app/modules/proxy/load_balancer.py` (`_state_from_account`), `app/core/balancer/logic.py` (`_select_relative_availability` fallback, possibly `_seeded_least_used`).
- Related measurement: `context.md` records a Plus-plan measurement of quota consumption against model-priced tokens, posted as corroborating data on #2420.
- Operators: until this ships, setting the dashboard **lease token weight** to `0` removes the mis-scaled term. The in-flight stream/create penalty (2.5% each by default) still applies.
